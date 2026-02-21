//! Claude executor adapter — spawns a Node.js wrapper that drives the Claude
//! Agent SDK, communicating over stdin/stdout JSON Lines.
//!
//! Background task pattern: `start()` spawns a tokio task that owns the child
//! process and bridges between AgentCommand/AgentEvent channels and the
//! subprocess stdin/stdout.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin};
use tokio::sync::mpsc;

use super::{
    AgentCommand, AgentEvent, ErrorKind, ExecutorHandle, SessionConfig, StderrBuffer, TurnResult,
    build_output_message, new_stderr_buffer, push_stderr_line, snapshot_stderr,
};

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

/// Start the Claude executor: spawn the Node.js subprocess and a background
/// task, returning an ExecutorHandle for channel-based communication.
pub async fn start(config: SessionConfig) -> Result<ExecutorHandle> {
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
        .with_context(|| format!("failed to spawn claude executor: {node_path} {script_path}"))?;

    let stdin = child.stdin.take().context("failed to get stdin")?;
    let stdout = child.stdout.take().context("failed to get stdout")?;
    let stderr = child.stderr.take().context("failed to get stderr")?;

    // Stdout reader: parse JSON Lines, validate, forward to channel
    let (raw_event_tx, raw_event_rx) = mpsc::unbounded_channel();
    let reader_handle = tokio::spawn(async move {
        let reader = BufReader::new(stdout);
        let mut lines = reader.lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if line.trim().is_empty() {
                continue;
            }

            let value: serde_json::Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(_) => {
                    tracing::warn!(line_len = line.len(), "ignoring non-JSON stdout line");
                    continue;
                }
            };

            let type_field = match value.get("type").and_then(|t| t.as_str()) {
                Some(t) => t.to_string(),
                None => {
                    tracing::warn!("ignoring unrecognized JSON on stdout");
                    continue;
                }
            };

            match type_field.as_str() {
                "init" | "message" | "result" | "error" => {}
                other => {
                    tracing::warn!(type_field = %other, "ignoring unknown event type");
                    continue;
                }
            }

            if raw_event_tx.send(value).is_err() {
                break;
            }
        }
    });

    // Stderr drain: capture to buffer + forward to tracing
    let stderr_buf = new_stderr_buffer();
    let buf_clone = stderr_buf.clone();
    tokio::spawn(async move {
        let reader = BufReader::new(stderr);
        let mut lines = reader.lines();
        while let Ok(Some(line)) = lines.next_line().await {
            tracing::debug!(target: "claude_executor", "{}", line);
            push_stderr_line(&buf_clone, line);
        }
    });

    tracing::info!(
        execution_id = %config.execution_id,
        "Claude executor started"
    );

    // Build MCP servers config
    let mcp_url = format!("{}/mcp", config.scheduler_url.trim_end_matches('/'));
    let mcp_servers = serde_json::json!({
        "beacon": {
            "type": "http",
            "url": mcp_url,
            "headers": {
                "Authorization": format!("Bearer {}", config.session_id)
            }
        }
    });

    // Channels for the worker main loop
    let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();
    let (event_tx, event_rx) = mpsc::unbounded_channel();

    // Background task: bridges between cmd_rx/event_tx and the subprocess
    let task_handle = tokio::spawn(background_task(
        child,
        stdin,
        raw_event_rx,
        reader_handle,
        cmd_rx,
        event_tx,
        config.cwd,
        mcp_servers,
        claude_config,
        stderr_buf,
    ));

    Ok(ExecutorHandle {
        cmd_tx,
        event_rx,
        task_handle,
    })
}

/// Background task that owns the child process and bridges channels.
#[allow(clippy::too_many_arguments)]
async fn background_task(
    mut child: Child,
    mut stdin: ChildStdin,
    mut raw_event_rx: mpsc::UnboundedReceiver<serde_json::Value>,
    reader_handle: tokio::task::JoinHandle<()>,
    mut cmd_rx: mpsc::UnboundedReceiver<AgentCommand>,
    event_tx: mpsc::UnboundedSender<AgentEvent>,
    cwd: String,
    mcp_servers: serde_json::Value,
    claude_config: ClaudeConfig,
    stderr_buf: StderrBuffer,
) {
    let mut agent_session_id: Option<String> = None;
    let mut last_content: Option<serde_json::Value> = None;
    let mut started = false;
    let mut pending_prompts: Vec<String> = Vec::new();

    loop {
        tokio::select! {
            biased;

            // Process raw stdout events from the reader task
            raw_event = raw_event_rx.recv() => {
                match raw_event {
                    Some(event) => {
                        let type_field = event.get("type").and_then(|t| t.as_str()).unwrap_or("");

                        match type_field {
                            "init" => {
                                if let Ok(init) = serde_json::from_value::<InitEvent>(event) {
                                    agent_session_id = Some(init.session_id.clone());
                                    started = true;
                                    let _ = event_tx.send(AgentEvent::Init { session_id: init.session_id });

                                    // Flush any prompts that arrived before init
                                    for text in pending_prompts.drain(..) {
                                        let cmd = ClaudeCommand::Prompt { text };
                                        if let Err(e) = write_command(&mut stdin, &cmd).await {
                                            tracing::warn!(error = %e, "failed to write buffered prompt");
                                        }
                                    }
                                } else {
                                    tracing::warn!("malformed init event");
                                }
                            }
                            "message" => {
                                if let Ok(msg) = serde_json::from_value::<MessageEvent>(event)
                                    && let Some(ref content) = msg.content
                                    && let Some(structured) = build_output_message(content)
                                {
                                    let _ = event_tx.send(AgentEvent::Message { output: structured.clone() });
                                    last_content = Some(structured);
                                }
                            }
                            "result" => match serde_json::from_value::<ResultEvent>(event) {
                                Ok(result) => {
                                    if let Some(ref sid) = result.session_id {
                                        agent_session_id = Some(sid.clone());
                                    }
                                    let mut turn = map_result_to_turn(
                                        result,
                                        agent_session_id.clone(),
                                        last_content.take(),
                                    );
                                    if turn.error.is_some() {
                                        turn.stderr = snapshot_stderr(&stderr_buf);
                                    }
                                    let _ = event_tx.send(AgentEvent::TurnComplete(turn));
                                }
                                Err(e) => {
                                    let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                        agent_session_id: agent_session_id.clone(),
                                        error: Some(format!("malformed result event: {e}")),
                                        error_kind: Some(ErrorKind::ExecutorFailed),
                                        output: None,
                                        stderr: snapshot_stderr(&stderr_buf),
                                    }));
                                }
                            },
                            "error" => {
                                last_content = None;
                                match serde_json::from_value::<ErrorEvent>(event) {
                                    Ok(err) => {
                                        let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                            agent_session_id: agent_session_id.clone(),
                                            error: Some(err.message),
                                            error_kind: Some(ErrorKind::ExecutorFailed),
                                            output: None,
                                            stderr: snapshot_stderr(&stderr_buf),
                                        }));
                                    }
                                    Err(e) => {
                                        let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                            agent_session_id: agent_session_id.clone(),
                                            error: Some(format!("malformed error event: {e}")),
                                            error_kind: Some(ErrorKind::ExecutorFailed),
                                            output: None,
                                            stderr: snapshot_stderr(&stderr_buf),
                                        }));
                                    }
                                }
                            }
                            _ => {}
                        }
                    }
                    None => {
                        // Reader channel closed — process died
                        let exit_status = child.try_wait();
                        let exit_info = match exit_status {
                            Ok(Some(status)) => format!("exit code: {status}"),
                            Ok(None) => "still running".to_string(),
                            Err(e) => format!("error checking status: {e}"),
                        };
                        let _ = event_tx.send(AgentEvent::ProcessDied {
                            error: format!("claude executor process died ({exit_info})"),
                            stderr: snapshot_stderr(&stderr_buf),
                        });
                        break;
                    }
                }
            }

            // Process commands from the worker main loop
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(AgentCommand::Start(task_payload)) => {
                        match super::extract_prompt_text(&task_payload) {
                            Ok(prompt_text) => {
                                let cmd = ClaudeCommand::Start {
                                    prompt: prompt_text,
                                    cwd: cwd.clone(),
                                    mcp_servers: Some(mcp_servers.clone()),
                                    model: claude_config.model.clone(),
                                    max_turns: claude_config.max_turns,
                                    max_budget_usd: claude_config.max_budget_usd,
                                    system_prompt: claude_config.system_prompt.clone(),
                                };
                                if let Err(e) = write_command(&mut stdin, &cmd).await {
                                    let _ = event_tx.send(AgentEvent::ProcessDied {
                                        error: format!("failed to write start command: {e}"),
                                        stderr: snapshot_stderr(&stderr_buf),
                                    });
                                    break;
                                }
                            }
                            Err(e) => {
                                let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                    agent_session_id: agent_session_id.clone(),
                                    error: Some(format!("bad task payload: {e}")),
                                    error_kind: Some(ErrorKind::ExecutorFailed),
                                    output: None,
                                    stderr: None,
                                }));
                            }
                        }
                    }
                    Some(AgentCommand::Prompt(text)) => {
                        last_content = None;
                        if !started {
                            // Buffer until agent is initialized
                            pending_prompts.push(text);
                        } else {
                            let cmd = ClaudeCommand::Prompt { text };
                            if let Err(e) = write_command(&mut stdin, &cmd).await {
                                tracing::warn!(error = %e, "failed to write prompt command");
                            }
                        }
                    }
                    Some(AgentCommand::Cancel) => {
                        let _ = write_command(&mut stdin, &ClaudeCommand::Cancel).await;
                    }
                    Some(AgentCommand::Stop) => {
                        let _ = write_command(&mut stdin, &ClaudeCommand::Stop).await;
                        // Close stdin to signal EOF
                        drop(stdin);
                        // Wait for process with timeout
                        let timeout = std::time::Duration::from_secs(5);
                        match tokio::time::timeout(timeout, child.wait()).await {
                            Ok(Ok(_)) => {}
                            Ok(Err(e)) => {
                                tracing::warn!(error = %e, "error waiting for claude executor to exit");
                            }
                            Err(_) => {
                                tracing::warn!("claude executor did not exit within timeout, killing");
                                let _ = child.kill().await;
                            }
                        }
                        let _ = reader_handle.await;
                        return;
                    }
                    None => {
                        // cmd_rx closed — worker dropped cmd_tx, shut down
                        drop(stdin);
                        let timeout = std::time::Duration::from_secs(5);
                        match tokio::time::timeout(timeout, child.wait()).await {
                            Ok(_) => {}
                            Err(_) => { let _ = child.kill().await; }
                        }
                        let _ = reader_handle.await;
                        return;
                    }
                }
            }
        }
    }

    // If we broke out of the loop (process died), clean up
    let timeout = std::time::Duration::from_secs(2);
    let _ = tokio::time::timeout(timeout, child.wait()).await;
    let _ = reader_handle.await;
}

async fn write_command(stdin: &mut ChildStdin, cmd: &ClaudeCommand) -> Result<()> {
    let json = serde_json::to_string(cmd)?;
    stdin.write_all(json.as_bytes()).await?;
    stdin.write_all(b"\n").await?;
    stdin.flush().await?;
    Ok(())
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

    // last_content is already in structured format from build_output_message()
    let output = if error.is_none() {
        result
            .result
            .map(|text| serde_json::json!({"role": "agent", "parts": [{"kind": "text", "text": text}]}))
            .or(last_content)
    } else {
        None
    };

    TurnResult {
        agent_session_id,
        error,
        error_kind,
        output,
        stderr: None, // caller attaches stderr snapshot when needed
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
