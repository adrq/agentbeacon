pub mod acp;
pub mod claude;
pub mod copilot;

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;

/// Bounded ring buffer for capturing subprocess stderr lines.
/// Uses std::sync::Mutex (not tokio) since the critical section is trivially short.
pub type StderrBuffer = Arc<Mutex<VecDeque<String>>>;

const STDERR_BUFFER_CAPACITY: usize = 100;

pub fn new_stderr_buffer() -> StderrBuffer {
    Arc::new(Mutex::new(VecDeque::with_capacity(STDERR_BUFFER_CAPACITY)))
}

/// Snapshot stderr buffer contents into a single string, or None if empty.
pub fn snapshot_stderr(buf: &StderrBuffer) -> Option<String> {
    let lines: Vec<String> = buf
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .cloned()
        .collect();
    if lines.is_empty() {
        None
    } else {
        Some(lines.join("\n"))
    }
}

/// Append a line to the stderr buffer, evicting oldest if at capacity.
pub fn push_stderr_line(buf: &StderrBuffer, line: String) {
    let mut guard = buf.lock().unwrap_or_else(|e| e.into_inner());
    if guard.len() >= STDERR_BUFFER_CAPACITY {
        guard.pop_front();
    }
    guard.push_back(line);
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    ExecutorFailed,
    Cancelled,
    BudgetExceeded,
    MaxTurns,
}

impl ErrorKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            ErrorKind::ExecutorFailed => "executor_failed",
            ErrorKind::Cancelled => "cancelled",
            ErrorKind::BudgetExceeded => "budget_exceeded",
            ErrorKind::MaxTurns => "max_turns",
        }
    }
}

#[allow(dead_code)]
pub struct SessionConfig {
    pub session_id: String,
    pub execution_id: String,
    pub agent_type: String,
    pub agent_config: serde_json::Value,
    pub sandbox_config: serde_json::Value,
    pub cwd: String,
    pub scheduler_url: String,
    /// Override for Node.js binary path (stdio-bridge executors)
    pub node_path: Option<String>,
    /// Override for executors/dist directory (stdio-bridge executors)
    pub executors_dir: Option<String>,
}

pub struct TurnResult {
    pub agent_session_id: Option<String>,
    pub error: Option<String>,
    pub error_kind: Option<ErrorKind>,
    pub output: Option<serde_json::Value>,
    pub stderr: Option<String>,
}

/// Events emitted by the agent process
pub enum AgentEvent {
    /// Agent SDK initialized with a session ID
    Init { session_id: String },
    /// Agent produced output (message content) — informational only,
    /// the background task accumulates these internally and includes
    /// the final message in TurnComplete(TurnResult.output).
    #[allow(dead_code)]
    Message { output: serde_json::Value },
    /// Agent turn completed (success or error).
    /// TurnResult.output contains the accumulated last_content from
    /// all Message events during the turn.
    TurnComplete(TurnResult),
    /// Agent process died unexpectedly
    ProcessDied {
        error: String,
        stderr: Option<String>,
    },
}

/// Commands sent to the agent process
pub enum AgentCommand {
    /// Initial prompt with full config (task_payload JSON)
    Start(serde_json::Value),
    /// Follow-up prompt (user message, handoff result, etc.)
    Prompt(String),
    /// Cancel current turn
    Cancel,
    /// Graceful shutdown
    Stop,
}

/// Returned by start_executor() — channels for async communication
pub struct ExecutorHandle {
    /// Send commands to the agent (Clone + Send, can be used from any task)
    pub cmd_tx: mpsc::UnboundedSender<AgentCommand>,
    /// Receive events from the agent
    pub event_rx: mpsc::UnboundedReceiver<AgentEvent>,
    /// Join handle for the background executor task
    pub task_handle: tokio::task::JoinHandle<()>,
}

/// Extract prompt text from task_payload — shared by stdio-bridge executors (Claude, Copilot).
/// ACP has its own extraction logic (different wire format).
pub(crate) fn extract_prompt_text(task_payload: &serde_json::Value) -> Result<String> {
    if let Some(message) = task_payload.get("message") {
        let parts = message
            .get("parts")
            .and_then(|p| p.as_array())
            .ok_or_else(|| anyhow::anyhow!("message missing parts array"))?;
        let texts: Vec<&str> = parts
            .iter()
            .filter_map(|p| {
                if p.get("kind").and_then(|k| k.as_str()) == Some("text") {
                    p.get("text").and_then(|t| t.as_str())
                } else {
                    None
                }
            })
            .collect();
        Ok(texts.join("\n"))
    } else if let Some(text) = task_payload.as_str() {
        Ok(text.to_string())
    } else {
        Err(anyhow::anyhow!(
            "unsupported task_payload format: expected object with message or string"
        ))
    }
}

/// Factory: create ExecutorHandle based on agent_type from task payload.
pub async fn start_executor(config: SessionConfig) -> Result<ExecutorHandle> {
    match config.agent_type.as_str() {
        "acp" => acp::start(config).await,
        "claude_sdk" => claude::start(config).await,
        "copilot_sdk" => copilot::start(config).await,
        other => Err(anyhow::anyhow!("unsupported agent_type: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_stderr_buffer_is_empty() {
        let buf = new_stderr_buffer();
        assert!(buf.lock().unwrap().is_empty());
        assert!(snapshot_stderr(&buf).is_none());
    }

    #[test]
    fn test_push_stderr_line_and_snapshot() {
        let buf = new_stderr_buffer();
        push_stderr_line(&buf, "line 1".into());
        push_stderr_line(&buf, "line 2".into());

        let snap = snapshot_stderr(&buf).unwrap();
        assert_eq!(snap, "line 1\nline 2");
    }

    #[test]
    fn test_stderr_buffer_bounded() {
        let buf = new_stderr_buffer();
        for i in 0..STDERR_BUFFER_CAPACITY + 50 {
            push_stderr_line(&buf, format!("line {i}"));
        }

        let locked = buf.lock().unwrap();
        assert_eq!(locked.len(), STDERR_BUFFER_CAPACITY);
        // Oldest lines should have been evicted
        assert!(locked.front().unwrap().starts_with("line 50"));
    }

    #[test]
    fn test_snapshot_stderr_after_poison_recovery() {
        let buf = new_stderr_buffer();
        push_stderr_line(&buf, "before".into());

        // Poison the mutex
        let buf_clone = buf.clone();
        let _ = std::thread::spawn(move || {
            let _guard = buf_clone.lock().unwrap();
            panic!("intentional poison");
        })
        .join();

        // Should still work via into_inner recovery
        push_stderr_line(&buf, "after".into());
        let snap = snapshot_stderr(&buf).unwrap();
        assert!(snap.contains("after"));
    }
}
