//! Claude executor adapter — spawns a Node.js wrapper that drives the Claude
//! Agent SDK, communicating over stdin/stdout JSON Lines.

use anyhow::{Context, Result};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin};
use tokio::sync::mpsc;

use super::{AgentHandle, ErrorKind, SessionConfig, TurnResult};

// --- Protocol types (Rust ↔ Node JSON Lines) ---

#[derive(Debug, Serialize)]
#[serde(tag = "type")]
enum ClaudeCommand {
    #[serde(rename = "start", rename_all = "camelCase")]
    Start {
        prompt: String,
        cwd: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        mcp_servers: Option<serde_json::Value>,
        #[serde(skip_serializing_if = "Option::is_none")]
        model: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        max_turns: Option<u32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        max_budget_usd: Option<f64>,
        #[serde(skip_serializing_if = "Option::is_none")]
        system_prompt: Option<String>,
    },
    #[serde(rename = "prompt", rename_all = "camelCase")]
    Prompt { text: String },
    #[serde(rename = "cancel")]
    Cancel,
    #[serde(rename = "stop")]
    Stop,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct InitEvent {
    session_id: String,
    #[allow(dead_code)]
    mcp_servers: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
#[allow(dead_code)] // Fields deserialized for protocol fidelity, used selectively
struct ResultEvent {
    subtype: String,
    session_id: Option<String>,
    result: Option<String>,
    errors: Option<Vec<String>>,
    cost_usd: Option<f64>,
    num_turns: Option<u32>,
    duration_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct ErrorEvent {
    message: String,
}

#[derive(Debug, Deserialize)]
struct MessageEvent {
    #[allow(dead_code)]
    role: String,
    content: Option<serde_json::Value>,
}

// --- Config ---

#[derive(Debug, Deserialize)]
pub struct ClaudeConfig {
    pub model: Option<String>,
    pub max_turns: Option<u32>,
    pub max_budget_usd: Option<f64>,
    pub system_prompt: Option<String>,
}

// --- Handle ---

pub struct ClaudeAgentHandle {
    child: Child,
    stdin: Option<ChildStdin>,
    event_rx: mpsc::UnboundedReceiver<serde_json::Value>,
    reader_handle: tokio::task::JoinHandle<()>,
    agent_session_id: Option<String>,
    first_prompt: bool,
    session_config: HandleConfig,
}

struct HandleConfig {
    cwd: String,
    scheduler_url: String,
    session_id: String,
    claude_config: ClaudeConfig,
}

impl ClaudeAgentHandle {
    pub async fn start(config: SessionConfig) -> Result<Self> {
        let claude_config: ClaudeConfig = serde_json::from_value(config.agent_config.clone())
            .context("failed to parse Claude agent config")?;

        let node_path = config
            .node_path
            .clone()
            .or_else(|| std::env::var("AGENTBEACON_NODE_PATH").ok())
            .unwrap_or_else(|| "node".to_string());
        let executors_dir = config
            .executors_dir
            .clone()
            .or_else(|| std::env::var("AGENTBEACON_EXECUTORS_DIR").ok())
            .context("AGENTBEACON_EXECUTORS_DIR must be set (via --executors-dir or env var)")?;

        let script_path = format!("{}/claude-executor.js", executors_dir);

        let mut child = tokio::process::Command::new(&node_path)
            .arg(&script_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .with_context(|| {
                format!("failed to spawn claude executor: {node_path} {script_path}")
            })?;

        let stdin = child.stdin.take().context("failed to get stdin")?;
        let stdout = child.stdout.take().context("failed to get stdout")?;
        let stderr = child.stderr.take().context("failed to get stderr")?;

        // Stdout reader: parse JSON Lines, validate, forward to channel
        let (event_tx, event_rx) = mpsc::unbounded_channel();
        let reader_handle = tokio::spawn(async move {
            let reader = BufReader::new(stdout);
            let mut lines = reader.lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if line.trim().is_empty() {
                    continue;
                }

                // Level 1: valid JSON?
                let value: serde_json::Value = match serde_json::from_str(&line) {
                    Ok(v) => v,
                    Err(_) => {
                        tracing::warn!(line_len = line.len(), "ignoring non-JSON stdout line");
                        continue;
                    }
                };

                // Level 2: has "type" field?
                let type_field = match value.get("type").and_then(|t| t.as_str()) {
                    Some(t) => t.to_string(),
                    None => {
                        tracing::warn!("ignoring unrecognized JSON on stdout");
                        continue;
                    }
                };

                // Level 3: known type?
                match type_field.as_str() {
                    "init" | "message" | "result" | "error" => {}
                    other => {
                        tracing::warn!(type_field = %other, "ignoring unknown event type");
                        continue;
                    }
                }

                // Level 4: forward (typed deserialization happens in send_prompt)
                if event_tx.send(value).is_err() {
                    break;
                }
            }
        });

        // Stderr drain: forward to tracing at debug level
        tokio::spawn(async move {
            let reader = BufReader::new(stderr);
            let mut lines = reader.lines();
            while let Ok(Some(line)) = lines.next_line().await {
                tracing::debug!(target: "claude_executor", "{}", line);
            }
        });

        tracing::info!(
            execution_id = %config.execution_id,
            "Claude executor started"
        );

        Ok(Self {
            child,
            stdin: Some(stdin),
            event_rx,
            reader_handle,
            agent_session_id: None,
            first_prompt: true,
            session_config: HandleConfig {
                cwd: config.cwd,
                scheduler_url: config.scheduler_url,
                session_id: config.session_id,
                claude_config,
            },
        })
    }

    async fn write_command(&mut self, cmd: &ClaudeCommand) -> Result<()> {
        let stdin = self.stdin.as_mut().context("stdin already closed")?;
        let json = serde_json::to_string(cmd)?;
        stdin.write_all(json.as_bytes()).await?;
        stdin.write_all(b"\n").await?;
        stdin.flush().await?;
        Ok(())
    }

    /// Extract prompt text from task_payload (same pattern as ACP adapter).
    fn extract_prompt_text(task_payload: &serde_json::Value) -> Result<String> {
        if let Some(message) = task_payload.get("message") {
            // Object with message.parts (A2A format)
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

    /// Build MCP servers config for the start command.
    fn build_mcp_servers(&self) -> serde_json::Value {
        let mcp_url = format!(
            "{}/mcp",
            self.session_config.scheduler_url.trim_end_matches('/')
        );
        serde_json::json!({
            "beacon": {
                "type": "http",
                "url": mcp_url,
                "headers": {
                    "Authorization": format!("Bearer {}", self.session_config.session_id)
                }
            }
        })
    }
}

#[async_trait]
impl AgentHandle for ClaudeAgentHandle {
    async fn send_prompt(&mut self, task_payload: &serde_json::Value) -> Result<TurnResult> {
        let prompt_text = Self::extract_prompt_text(task_payload)?;

        if self.first_prompt {
            self.first_prompt = false;
            let cmd = ClaudeCommand::Start {
                prompt: prompt_text,
                cwd: self.session_config.cwd.clone(),
                mcp_servers: Some(self.build_mcp_servers()),
                model: self.session_config.claude_config.model.clone(),
                max_turns: self.session_config.claude_config.max_turns,
                max_budget_usd: self.session_config.claude_config.max_budget_usd,
                system_prompt: self.session_config.claude_config.system_prompt.clone(),
            };
            self.write_command(&cmd).await?;
        } else {
            let cmd = ClaudeCommand::Prompt { text: prompt_text };
            self.write_command(&cmd).await?;
        }

        // Read events until result or error
        let mut last_content: Option<serde_json::Value> = None;

        loop {
            match self.event_rx.recv().await {
                Some(event) => {
                    let type_field = event.get("type").and_then(|t| t.as_str()).unwrap_or("");

                    match type_field {
                        "init" => {
                            if let Ok(init) = serde_json::from_value::<InitEvent>(event) {
                                self.agent_session_id = Some(init.session_id);
                            } else {
                                tracing::warn!("malformed init event");
                            }
                        }
                        "message" => {
                            if let Ok(msg) = serde_json::from_value::<MessageEvent>(event)
                                && let Some(content) = msg.content
                            {
                                last_content = Some(content);
                            }
                        }
                        "result" => match serde_json::from_value::<ResultEvent>(event) {
                            Ok(result) => {
                                if let Some(ref sid) = result.session_id {
                                    self.agent_session_id = Some(sid.clone());
                                }
                                return Ok(map_result_to_turn(
                                    result,
                                    self.agent_session_id.clone(),
                                    last_content,
                                ));
                            }
                            Err(e) => {
                                return Ok(TurnResult {
                                    agent_session_id: self.agent_session_id.clone(),

                                    error: Some(format!("malformed result event: {e}")),
                                    error_kind: Some(ErrorKind::ExecutorFailed),
                                    output: None,
                                });
                            }
                        },
                        "error" => match serde_json::from_value::<ErrorEvent>(event) {
                            Ok(err) => {
                                return Ok(TurnResult {
                                    agent_session_id: self.agent_session_id.clone(),

                                    error: Some(err.message),
                                    error_kind: Some(ErrorKind::ExecutorFailed),
                                    output: None,
                                });
                            }
                            Err(e) => {
                                return Ok(TurnResult {
                                    agent_session_id: self.agent_session_id.clone(),

                                    error: Some(format!("malformed error event: {e}")),
                                    error_kind: Some(ErrorKind::ExecutorFailed),
                                    output: None,
                                });
                            }
                        },
                        _ => {}
                    }
                }
                None => {
                    // Channel closed — process died
                    let exit_status = self.child.try_wait();
                    let exit_info = match exit_status {
                        Ok(Some(status)) => format!("exit code: {status}"),
                        Ok(None) => "still running".to_string(),
                        Err(e) => format!("error checking status: {e}"),
                    };
                    return Ok(TurnResult {
                        agent_session_id: self.agent_session_id.clone(),
                        error: Some(format!("claude executor process died ({exit_info})")),
                        error_kind: Some(ErrorKind::ExecutorFailed),
                        output: None,
                    });
                }
            }
        }
    }

    async fn cancel(&mut self) -> Result<()> {
        // Best-effort: swallow errors if stdin is already closed
        let _ = self.write_command(&ClaudeCommand::Cancel).await;
        Ok(())
    }

    async fn stop(&mut self) -> Result<()> {
        if self.stdin.is_some() {
            let _ = self.write_command(&ClaudeCommand::Stop).await;
        }
        // Close stdin to signal EOF
        self.stdin = None;

        // Wait for process with timeout
        let timeout = std::time::Duration::from_secs(5);
        match tokio::time::timeout(timeout, self.child.wait()).await {
            Ok(Ok(_)) => {}
            Ok(Err(e)) => {
                tracing::warn!(error = %e, "error waiting for claude executor to exit");
            }
            Err(_) => {
                tracing::warn!("claude executor did not exit within timeout, killing");
                let _ = self.child.kill().await;
            }
        }

        let _ = (&mut self.reader_handle).await;
        Ok(())
    }
}

/// Map SDK result subtypes to TurnResult with ErrorKind.
fn map_result_to_turn(
    result: ResultEvent,
    agent_session_id: Option<String>,
    last_content: Option<serde_json::Value>,
) -> TurnResult {
    let (error, error_kind) = match result.subtype.as_str() {
        "success" => (None, None),
        "error_max_turns" => (
            Some(
                result
                    .errors
                    .as_ref()
                    .map_or("max turns reached".into(), |e| e.join("; ")),
            ),
            Some(ErrorKind::MaxTurns),
        ),
        "error_max_budget_usd" => (
            Some(
                result
                    .errors
                    .as_ref()
                    .map_or("budget exceeded".into(), |e| e.join("; ")),
            ),
            Some(ErrorKind::BudgetExceeded),
        ),
        "cancelled" => (Some("session cancelled".into()), Some(ErrorKind::Cancelled)),
        // error_during_execution, error_max_structured_output_retries, or unknown
        other => (
            Some(
                result
                    .errors
                    .as_ref()
                    .map_or(format!("executor error: {other}"), |e| e.join("; ")),
            ),
            Some(ErrorKind::ExecutorFailed),
        ),
    };

    // Build output in the same shape as ACP adapter: {"role": "agent", "parts": [...]}
    let output = if error.is_none() {
        result
            .result
            .map(|text| serde_json::json!({"role": "agent", "parts": [{"kind": "text", "text": text}]}))
            .or_else(|| last_content.and_then(|c| {
                // Extract text from SDK content blocks (array of {type: "text", text: "..."})
                if let serde_json::Value::Array(items) = c {
                    let parts: Vec<serde_json::Value> = items.into_iter().filter_map(|item| {
                        let text = item.get("text").and_then(|t| t.as_str()).unwrap_or("");
                        if text.is_empty() { return None; }
                        Some(serde_json::json!({"kind": "text", "text": text}))
                    }).collect();
                    if parts.is_empty() { return None; }
                    Some(serde_json::json!({"role": "agent", "parts": parts}))
                } else {
                    Some(serde_json::json!({"role": "agent", "parts": [{"kind": "text", "text": c.to_string()}]}))
                }
            }))
    } else {
        None
    };

    TurnResult {
        agent_session_id,
        error,
        error_kind,
        output,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_parse_init_event() {
        let json_str = r#"{"type":"init","sessionId":"abc-123","mcpServers":[{"name":"beacon","status":"connected"}]}"#;
        let value: serde_json::Value = serde_json::from_str(json_str).unwrap();
        let init: InitEvent = serde_json::from_value(value).unwrap();
        assert_eq!(init.session_id, "abc-123");
    }

    #[test]
    fn test_parse_result_success() {
        let value = json!({
            "type": "result",
            "subtype": "success",
            "sessionId": "abc-123",
            "result": "Done. Created auth.py.",
            "costUsd": 0.42,
            "numTurns": 3,
            "durationMs": 15000
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(result, Some("abc-123".into()), None);
        assert!(turn.error.is_none());
        assert!(turn.error_kind.is_none());
        assert!(turn.output.is_some());
    }

    #[test]
    fn test_parse_result_error_max_turns() {
        let value = json!({
            "type": "result",
            "subtype": "error_max_turns",
            "sessionId": "abc-123",
            "errors": ["Hit 50 turn limit"],
            "costUsd": 2.10,
            "numTurns": 50
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::MaxTurns));
    }

    #[test]
    fn test_parse_result_error_max_budget() {
        let value = json!({
            "type": "result",
            "subtype": "error_max_budget_usd",
            "sessionId": "abc-123",
            "errors": ["Budget exceeded"],
            "costUsd": 5.0,
            "numTurns": 12
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::BudgetExceeded));
    }

    #[test]
    fn test_parse_unknown_type_ignored() {
        let value = json!({"type": "stream_event", "data": "partial"});
        let type_field = value.get("type").and_then(|t| t.as_str()).unwrap();
        let known = matches!(type_field, "init" | "message" | "result" | "error");
        assert!(!known);
    }

    #[test]
    fn test_parse_non_json_ignored() {
        let result = serde_json::from_str::<serde_json::Value>("not json at all");
        assert!(result.is_err());
    }

    #[test]
    fn test_start_command_serialization() {
        let cmd = ClaudeCommand::Start {
            prompt: "Hello".into(),
            cwd: "/workspace".into(),
            mcp_servers: Some(
                json!({"beacon": {"type": "http", "url": "http://localhost:9456/mcp"}}),
            ),
            model: Some("claude-sonnet-4-5-20250929".into()),
            max_turns: Some(50),
            max_budget_usd: Some(5.0),
            system_prompt: None,
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "start");
        assert_eq!(value["prompt"], "Hello");
        assert_eq!(value["cwd"], "/workspace");
        assert_eq!(value["maxTurns"], 50);
        assert_eq!(value["maxBudgetUsd"], 5.0);
        assert!(value.get("systemPrompt").is_none());
    }

    #[test]
    fn test_prompt_command_serialization() {
        let cmd = ClaudeCommand::Prompt {
            text: "continue with JWT".into(),
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "prompt");
        assert_eq!(value["text"], "continue with JWT");
    }

    #[test]
    fn test_error_kind_as_str() {
        assert_eq!(ErrorKind::ExecutorFailed.as_str(), "executor_failed");
        assert_eq!(ErrorKind::Cancelled.as_str(), "cancelled");
        assert_eq!(ErrorKind::BudgetExceeded.as_str(), "budget_exceeded");
        assert_eq!(ErrorKind::MaxTurns.as_str(), "max_turns");
    }

    #[test]
    fn test_result_cancelled_mapping() {
        let value = json!({
            "type": "result",
            "subtype": "cancelled",
            "sessionId": "abc-123",
            "costUsd": 0
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(result, Some("abc-123".into()), None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::Cancelled));
    }
}
