pub mod acp;
pub mod codex;
pub(crate) mod sdk;

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Duration;
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
    StoppedByUser,
    BudgetExceeded,
    MaxTurns,
}

impl ErrorKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            ErrorKind::ExecutorFailed => "executor_failed",
            ErrorKind::Cancelled => "cancelled",
            ErrorKind::StoppedByUser => "stopped_by_user",
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
    /// Path to executor JS files. Set from embedded extraction or AGENTBEACON_EXECUTORS_DIR override.
    pub executors_dir: Option<String>,
    /// Resolved node_modules path for SDK module resolution (embedded executor mode)
    pub node_modules_dir: Option<String>,
    /// Max time with no agent output during an active turn before killing it
    pub inactivity_timeout: Duration,
    pub project_id: Option<String>,
    /// User-configured MCP servers from project settings (merged with coordination server)
    pub user_mcp_servers: serde_json::Value,
    /// For resume: the agent_session_id (Codex thread_id) from the prior session.
    /// Used by the Codex executor to derive the correct CODEX_HOME path.
    pub resume_agent_session_id: Option<String>,
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
    /// SDK confirmed receipt of a prompt payload
    Accepted,
    /// Agent produced output (message content) during a turn.
    /// Forwarded to the scheduler in real-time by the worker main loop.
    /// When `ephemeral` is true, the message contains only streaming text deltas
    /// and should be delivered via SSE but NOT persisted to the DB.
    Message {
        output: serde_json::Value,
        ephemeral: bool,
    },
    /// Agent turn completed (success or error).
    /// TurnResult.output is taken from the executor's ResultEvent.result field.
    /// When `settled` is true, the turn is final. When false, more events may follow.
    TurnComplete { result: TurnResult, settled: bool },
    /// Agent process died unexpectedly.
    /// `agent_session_id` is set by Codex when the thread_id is known but Init hasn't
    /// fired yet (pre-Init turn/start failure). Claude/Copilot/ACP pass `None`.
    ProcessDied {
        error: String,
        stderr: Option<String>,
        agent_session_id: Option<String>,
    },
}

/// Commands sent to the agent process
pub enum AgentCommand {
    /// Initial prompt with full config (task_payload JSON)
    Start(serde_json::Value),
    /// Follow-up prompt (user message, turn-complete result, etc.)
    Prompt(Vec<serde_json::Value>),
    /// Cancel current turn
    Cancel,
    /// Stop current turn (user-initiated)
    StopTurn,
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
    /// PID of the executor child process, for signal escalation.
    /// None if the child exited before PID could be captured.
    pub child_pid: Option<u32>,
}

/// Map a single SDK content block to an A2A v1.0 message part.
/// Text blocks become `{"text": ...}`. Everything else passes through raw as
/// `{"data": ...}` — the frontend normalizer handles executor-specific fields.
pub fn content_block_to_part(item: &serde_json::Value) -> Option<serde_json::Value> {
    let block_type = item.get("type").and_then(|t| t.as_str()).unwrap_or("text");
    match block_type {
        "text" | "text_delta" => {
            let text = item.get("text").and_then(|t| t.as_str()).unwrap_or("");
            if text.is_empty() {
                return None;
            }
            Some(serde_json::json!({"text": text}))
        }
        _ => Some(serde_json::json!({"data": item})),
    }
}

/// Convert raw SDK content blocks into a structured `{role: "agent", parts: [...]}` message.
pub fn build_output_message(content_blocks: &serde_json::Value) -> Option<serde_json::Value> {
    let blocks = content_blocks.as_array()?;
    let parts: Vec<_> = blocks.iter().filter_map(content_block_to_part).collect();
    if parts.is_empty() {
        return None;
    }
    Some(serde_json::json!({"role": "ROLE_AGENT", "parts": parts}))
}

/// Extract full parts array from task_payload — used by stdio-bridge executors.
/// All payloads use A2A format: `{message: {role, parts}}`.
pub(crate) fn extract_parts(task_payload: &serde_json::Value) -> Result<Vec<serde_json::Value>> {
    let message = task_payload
        .get("message")
        .ok_or_else(|| anyhow::anyhow!("task_payload missing message field"))?;
    let parts = message
        .get("parts")
        .and_then(|p| p.as_array())
        .ok_or_else(|| anyhow::anyhow!("message missing parts array"))?;
    Ok(parts.clone())
}

/// Extract prompt text from task_payload (text parts only, joined with newlines).
/// Text headers (e.g. "[turn complete from ...]") are baked into parts by the producer.
#[allow(dead_code)]
pub(crate) fn extract_prompt_text(task_payload: &serde_json::Value) -> Result<String> {
    let parts = extract_parts(task_payload)?;
    let texts: Vec<&str> = parts
        .iter()
        .filter_map(|p| p.get("text").and_then(|t| t.as_str()))
        .collect();
    Ok(texts.join("\n"))
}

/// Factory: create ExecutorHandle based on agent_type from task payload.
pub async fn start_executor(config: SessionConfig) -> Result<ExecutorHandle> {
    match config.agent_type.as_str() {
        "acp" => acp::start(config).await,
        "claude_sdk" => sdk::start(sdk::SdkKind::Claude, config).await,
        "copilot_sdk" => sdk::start(sdk::SdkKind::Copilot, config).await,
        "codex_sdk" => codex::start(config).await,
        other => Err(anyhow::anyhow!("unsupported agent_type: {other}")),
    }
}
