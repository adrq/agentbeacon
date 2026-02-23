//! Copilot executor adapter — spawns a Node.js wrapper that drives the GitHub
//! Copilot SDK, communicating over stdin/stdout JSON Lines.
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
enum CopilotCommand {
    #[serde(rename = "start", rename_all = "camelCase")]
    Start {
        prompt: String,
        cwd: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        mcp_servers: Option<serde_json::Value>,
        #[serde(skip_serializing_if = "Option::is_none")]
        model: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        system_prompt: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        provider: Option<serde_json::Value>,
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
#[allow(dead_code)]
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
pub struct CopilotConfig {
    pub model: Option<String>,
    pub system_prompt: Option<String>,
    pub provider: Option<serde_json::Value>,
    pub api_key_env: Option<String>,
}

/// Start the Copilot executor: spawn the Node.js subprocess and a background
/// task, returning an ExecutorHandle for channel-based communication.
pub async fn start(config: SessionConfig) -> Result<ExecutorHandle> {
    let copilot_config: CopilotConfig = serde_json::from_value(config.agent_config.clone())
        .context("failed to parse Copilot agent config")?;

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

    let script_path = format!("{}/copilot-executor.js", executors_dir);

    // Standard Copilot auth (COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN)
    // inherited from parent process env automatically.
    let mut cmd = tokio::process::Command::new(&node_path);
    cmd.arg(&script_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);

    // Inject BYOK API key via process env if configured — NEVER over stdin
    if let Some(ref env_name) = copilot_config.api_key_env {
        match std::env::var(env_name) {
            Ok(key_value) => {
                cmd.env(env_name, key_value);
            }
            Err(_) => {
                tracing::warn!(env_var = %env_name, "BYOK api_key_env not found in environment");
            }
        }
    }

    let mut child = cmd
        .spawn()
        .with_context(|| format!("failed to spawn copilot executor: {node_path} {script_path}"))?;

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
            tracing::debug!(target: "copilot_executor", "{}", line);
            push_stderr_line(&buf_clone, line);
        }
    });

    tracing::info!(
        execution_id = %config.execution_id,
        "Copilot executor started"
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
        copilot_config,
        stderr_buf,
        config.inactivity_timeout,
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
    copilot_config: CopilotConfig,
    stderr_buf: StderrBuffer,
    inactivity_timeout: std::time::Duration,
) {
    let mut agent_session_id: Option<String> = None;
    let mut last_content: Option<serde_json::Value> = None;
    let mut started = false;
    let mut pending_prompts: Vec<String> = Vec::new();
    let mut turn_active = true; // starts active — processing initial prompt
    let mut last_activity = tokio::time::Instant::now();

    loop {
        tokio::select! {
            biased;

            // Process raw stdout events from the reader task
            raw_event = raw_event_rx.recv() => {
                match raw_event {
                    Some(event) => {
                        last_activity = tokio::time::Instant::now();
                        let type_field = event.get("type").and_then(|t| t.as_str()).unwrap_or("");

                        match type_field {
                            "init" => {
                                if let Ok(init) = serde_json::from_value::<InitEvent>(event) {
                                    agent_session_id = Some(init.session_id.clone());
                                    started = true;
                                    let _ = event_tx.send(AgentEvent::Init { session_id: init.session_id });

                                    // Flush any prompts that arrived before init
                                    for text in pending_prompts.drain(..) {
                                        let cmd = CopilotCommand::Prompt { text };
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
                                    turn_active = false;
                                    let _ = event_tx.send(AgentEvent::TurnComplete(turn));
                                }
                                Err(e) => {
                                    turn_active = false;
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
                                        turn_active = false;
                                        let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                            agent_session_id: agent_session_id.clone(),
                                            error: Some(err.message),
                                            error_kind: Some(ErrorKind::ExecutorFailed),
                                            output: None,
                                            stderr: snapshot_stderr(&stderr_buf),
                                        }));
                                    }
                                    Err(e) => {
                                        turn_active = false;
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
                            error: format!("copilot executor process died ({exit_info})"),
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
                        turn_active = true;
                        last_activity = tokio::time::Instant::now();
                        match super::extract_prompt_text(&task_payload) {
                            Ok(prompt_text) => {
                                let cmd = CopilotCommand::Start {
                                    prompt: prompt_text,
                                    cwd: cwd.clone(),
                                    mcp_servers: Some(mcp_servers.clone()),
                                    model: copilot_config.model.clone(),
                                    system_prompt: copilot_config.system_prompt.clone(),
                                    provider: sanitize_provider(copilot_config.provider.clone()),
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
                                turn_active = false;
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
                        turn_active = true;
                        last_activity = tokio::time::Instant::now();
                        last_content = None;
                        if !started {
                            pending_prompts.push(text);
                        } else {
                            let cmd = CopilotCommand::Prompt { text };
                            if let Err(e) = write_command(&mut stdin, &cmd).await {
                                tracing::warn!(error = %e, "failed to write prompt command");
                            }
                        }
                    }
                    Some(AgentCommand::Cancel) => {
                        let _ = write_command(&mut stdin, &CopilotCommand::Cancel).await;
                    }
                    Some(AgentCommand::Stop) => {
                        let _ = write_command(&mut stdin, &CopilotCommand::Stop).await;
                        drop(stdin);
                        let timeout = std::time::Duration::from_secs(5);
                        match tokio::time::timeout(timeout, child.wait()).await {
                            Ok(Ok(_)) => {}
                            Ok(Err(e)) => {
                                tracing::warn!(error = %e, "error waiting for copilot executor to exit");
                            }
                            Err(_) => {
                                tracing::warn!("copilot executor did not exit within timeout, killing");
                                let _ = child.kill().await;
                            }
                        }
                        let _ = reader_handle.await;
                        return;
                    }
                    None => {
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

            // Branch 3: Inactivity timeout (only during active turns)
            _ = tokio::time::sleep_until(last_activity + inactivity_timeout),
                if turn_active => {
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: format!("executor stalled: no output for {}s", inactivity_timeout.as_secs()),
                    stderr: snapshot_stderr(&stderr_buf),
                });
                let _ = child.kill().await;
                reader_handle.abort();
                return;
            }
        }
    }

    // If we broke out of the loop (process died), clean up
    let timeout = std::time::Duration::from_secs(2);
    let _ = tokio::time::timeout(timeout, child.wait()).await;
    let _ = reader_handle.await;
}

async fn write_command(stdin: &mut ChildStdin, cmd: &CopilotCommand) -> Result<()> {
    let json = serde_json::to_string(cmd)?;
    stdin.write_all(json.as_bytes()).await?;
    stdin.write_all(b"\n").await?;
    stdin.flush().await?;
    Ok(())
}

/// Strip sensitive fields from provider config before sending over stdin.
/// API keys should be injected via process env (api_key_env), not JSON Lines.
fn sanitize_provider(provider: Option<serde_json::Value>) -> Option<serde_json::Value> {
    const SENSITIVE_KEYS: &[&str] = &["apiKey", "api_key", "bearerToken", "bearer_token"];
    provider.map(|mut v| {
        if let serde_json::Value::Object(ref mut map) = v {
            for key in SENSITIVE_KEYS {
                if map.remove(*key).is_some() {
                    tracing::warn!(field = %key, "stripped sensitive field from provider config — use api_key_env instead");
                }
            }
        }
        v
    })
}

/// Map Copilot result subtypes to TurnResult with ErrorKind.
fn map_result_to_turn(
    result: ResultEvent,
    agent_session_id: Option<String>,
    last_content: Option<serde_json::Value>,
) -> TurnResult {
    let (error, error_kind) = match result.subtype.as_str() {
        "success" => (None, None),
        "cancelled" => (Some("session cancelled".into()), Some(ErrorKind::Cancelled)),
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
    fn test_copilot_config_parsing() {
        let json_val = json!({
            "model": "gpt-5",
            "system_prompt": "You are a helpful assistant."
        });
        let config: CopilotConfig = serde_json::from_value(json_val).unwrap();
        assert_eq!(config.model.as_deref(), Some("gpt-5"));
        assert_eq!(
            config.system_prompt.as_deref(),
            Some("You are a helpful assistant.")
        );
        assert!(config.provider.is_none());
        assert!(config.api_key_env.is_none());
    }

    #[test]
    fn test_copilot_config_with_provider() {
        let json_val = json!({
            "model": "gpt-5",
            "provider": {
                "type": "openai",
                "baseUrl": "https://api.openai.com/v1"
            },
            "api_key_env": "OPENAI_API_KEY"
        });
        let config: CopilotConfig = serde_json::from_value(json_val).unwrap();
        assert_eq!(config.model.as_deref(), Some("gpt-5"));
        let provider = config.provider.unwrap();
        assert_eq!(provider["type"], "openai");
        assert_eq!(provider["baseUrl"], "https://api.openai.com/v1");
        assert_eq!(config.api_key_env.as_deref(), Some("OPENAI_API_KEY"));
    }

    #[test]
    fn test_copilot_start_command_serialization() {
        let cmd = CopilotCommand::Start {
            prompt: "Fix the tests".into(),
            cwd: "/workspace".into(),
            mcp_servers: Some(
                json!({"beacon": {"type": "http", "url": "http://localhost:9456/mcp"}}),
            ),
            model: Some("gpt-5".into()),
            system_prompt: None,
            provider: Some(json!({"type": "openai", "baseUrl": "https://api.openai.com/v1"})),
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "start");
        assert_eq!(value["prompt"], "Fix the tests");
        assert_eq!(value["cwd"], "/workspace");
        assert_eq!(value["model"], "gpt-5");
        assert!(value.get("mcpServers").is_some());
        assert_eq!(value["provider"]["type"], "openai");
        assert!(value.get("systemPrompt").is_none());
    }

    #[test]
    fn test_copilot_prompt_command_serialization() {
        let cmd = CopilotCommand::Prompt {
            text: "Now run the tests".into(),
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "prompt");
        assert_eq!(value["text"], "Now run the tests");
    }

    #[test]
    fn test_parse_init_event() {
        let json_str = r#"{"type":"init","sessionId":"cop-123","mcpServers":[{"name":"beacon","status":"connected"}]}"#;
        let value: serde_json::Value = serde_json::from_str(json_str).unwrap();
        let init: InitEvent = serde_json::from_value(value).unwrap();
        assert_eq!(init.session_id, "cop-123");
    }

    #[test]
    fn test_parse_result_event_no_cost() {
        let value = json!({
            "type": "result",
            "subtype": "success",
            "result": "Done."
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        assert_eq!(result.subtype, "success");
        assert_eq!(result.result.as_deref(), Some("Done."));
        assert!(result.cost_usd.is_none());
        assert!(result.num_turns.is_none());
        assert!(result.duration_ms.is_none());
    }

    #[test]
    fn test_map_result_to_turn() {
        // success
        let result = ResultEvent {
            subtype: "success".into(),
            session_id: Some("cop-123".into()),
            result: Some("All tests pass.".into()),
            errors: None,
            cost_usd: None,
            num_turns: None,
            duration_ms: None,
        };
        let turn = map_result_to_turn(result, Some("cop-123".into()), None);
        assert!(turn.error.is_none());
        assert!(turn.error_kind.is_none());
        assert!(turn.output.is_some());

        // cancelled
        let result = ResultEvent {
            subtype: "cancelled".into(),
            session_id: Some("cop-123".into()),
            result: None,
            errors: None,
            cost_usd: None,
            num_turns: None,
            duration_ms: None,
        };
        let turn = map_result_to_turn(result, Some("cop-123".into()), None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::Cancelled));

        // error_during_execution
        let result = ResultEvent {
            subtype: "error_during_execution".into(),
            session_id: None,
            result: None,
            errors: Some(vec!["sendAndWait returned undefined".into()]),
            cost_usd: None,
            num_turns: None,
            duration_ms: None,
        };
        let turn = map_result_to_turn(result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::ExecutorFailed));
    }
}
