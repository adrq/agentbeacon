//! Unified SDK executor adapter — spawns a Node.js wrapper that drives either
//! the Claude Agent SDK or GitHub Copilot SDK, communicating over stdin/stdout
//! JSON Lines.
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

// --- SDK kind ---

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) enum SdkKind {
    Claude,
    Copilot,
}

impl SdkKind {
    fn script_name(self) -> &'static str {
        match self {
            SdkKind::Claude => "claude-executor.js",
            SdkKind::Copilot => "copilot-executor.js",
        }
    }

    fn label(self) -> &'static str {
        match self {
            SdkKind::Claude => "Claude",
            SdkKind::Copilot => "Copilot",
        }
    }

    fn tracing_target(self) -> &'static str {
        match self {
            SdkKind::Claude => "claude_executor",
            SdkKind::Copilot => "copilot_executor",
        }
    }
}

// --- Protocol types (Rust ↔ Node JSON Lines) ---

#[derive(Debug, Serialize)]
#[serde(tag = "type")]
enum SdkCommand {
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
        resume_session_id: Option<String>,
        // Claude-only
        #[serde(skip_serializing_if = "Option::is_none")]
        max_turns: Option<u32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        max_budget_usd: Option<f64>,
        // Copilot-only
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

// --- Config types ---

#[derive(Debug, Deserialize)]
pub struct ClaudeConfig {
    pub model: Option<String>,
    pub max_turns: Option<u32>,
    pub max_budget_usd: Option<f64>,
    pub system_prompt: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct CopilotConfig {
    pub model: Option<String>,
    pub system_prompt: Option<String>,
    pub provider: Option<serde_json::Value>,
    pub api_key_env: Option<String>,
}

enum ParsedConfig {
    Claude(ClaudeConfig),
    Copilot(CopilotConfig),
}

// --- Entry point ---

pub async fn start(kind: SdkKind, config: SessionConfig) -> Result<ExecutorHandle> {
    let parsed_config = match kind {
        SdkKind::Claude => ParsedConfig::Claude(
            serde_json::from_value(config.agent_config.clone())
                .context("failed to parse Claude agent config")?,
        ),
        SdkKind::Copilot => ParsedConfig::Copilot(
            serde_json::from_value(config.agent_config.clone())
                .context("failed to parse Copilot agent config")?,
        ),
    };

    let node_path = config
        .node_path
        .clone()
        .or_else(|| std::env::var("AGENTBEACON_NODE_PATH").ok())
        .unwrap_or_else(|| "node".to_string());
    let executors_dir = config
        .executors_dir
        .clone()
        .or_else(|| std::env::var("AGENTBEACON_EXECUTORS_DIR").ok())
        .context(
            "No executor directory available. Set AGENTBEACON_EXECUTORS_DIR for manual override.",
        )?;

    let script_path = format!("{}/{}", executors_dir, kind.script_name());

    let mut cmd = tokio::process::Command::new(&node_path);
    cmd.arg(&script_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);

    // Inject BYOK API key via process env if configured — NEVER over stdin
    if let ParsedConfig::Copilot(ref cc) = parsed_config
        && let Some(ref env_name) = cc.api_key_env
    {
        match std::env::var(env_name) {
            Ok(key_value) => {
                cmd.env(env_name, key_value);
            }
            Err(_) => {
                tracing::warn!(env_var = %env_name, "BYOK api_key_env not found in environment");
            }
        }
    }

    // Inject AgentBeacon environment variables for REST API access
    cmd.env("AGENTBEACON_SESSION_ID", &config.session_id);
    cmd.env("AGENTBEACON_API_BASE", &config.scheduler_url);
    cmd.env("AGENTBEACON_EXECUTION_ID", &config.execution_id);
    if let Some(ref pid) = config.project_id {
        cmd.env("AGENTBEACON_PROJECT_ID", pid);
    } else {
        cmd.env_remove("AGENTBEACON_PROJECT_ID");
    }
    if let Some(ref nm_dir) = config.node_modules_dir {
        cmd.env("NODE_PATH", nm_dir);
    }

    // Suppress SDK's nonessential HTTP traffic (telemetry, update checks, connectivity
    // pings) — these use hardcoded 5000ms timeouts that cause spurious AxiosErrors.
    // Not all ancillary calls are gated by this flag; the TS-side retry logic is the
    // primary defense.
    if kind == SdkKind::Claude {
        cmd.env("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1");
    }

    let mut child = cmd.spawn().with_context(|| {
        format!(
            "failed to spawn {} executor: {node_path} {script_path}",
            kind.label()
        )
    })?;

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
    let tracing_target = kind.tracing_target();
    tokio::spawn(async move {
        let reader = BufReader::new(stderr);
        let mut lines = reader.lines();
        while let Ok(Some(line)) = lines.next_line().await {
            // Cannot use a runtime variable as tracing target, so branch statically
            match tracing_target {
                "claude_executor" => tracing::debug!(target: "claude_executor", "{}", line),
                _ => tracing::debug!(target: "copilot_executor", "{}", line),
            }
            push_stderr_line(&buf_clone, line);
        }
    });

    tracing::info!(
        execution_id = %config.execution_id,
        "{} executor started", kind.label()
    );

    // Build MCP servers config
    let mcp_url = format!("{}/mcp", config.scheduler_url.trim_end_matches('/'));
    let mcp_servers = serde_json::json!({
        "agentbeacon": {
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
        kind,
        child,
        stdin,
        raw_event_rx,
        reader_handle,
        cmd_rx,
        event_tx,
        config.cwd,
        mcp_servers,
        parsed_config,
        stderr_buf,
        config.inactivity_timeout,
    ));

    Ok(ExecutorHandle {
        cmd_tx,
        event_rx,
        task_handle,
    })
}

// --- Build start command ---

fn build_start_command(
    prompt: String,
    cwd: &str,
    mcp_servers: &serde_json::Value,
    parsed_config: &ParsedConfig,
    resume_session_id: Option<String>,
) -> SdkCommand {
    match parsed_config {
        ParsedConfig::Claude(cc) => SdkCommand::Start {
            prompt,
            cwd: cwd.to_string(),
            mcp_servers: Some(mcp_servers.clone()),
            model: cc.model.clone(),
            system_prompt: cc.system_prompt.clone(),
            resume_session_id,
            max_turns: cc.max_turns,
            max_budget_usd: cc.max_budget_usd,
            provider: None,
        },
        ParsedConfig::Copilot(cc) => SdkCommand::Start {
            prompt,
            cwd: cwd.to_string(),
            mcp_servers: Some(mcp_servers.clone()),
            model: cc.model.clone(),
            system_prompt: cc.system_prompt.clone(),
            resume_session_id,
            max_turns: None,
            max_budget_usd: None,
            provider: sanitize_provider(cc.provider.clone()),
        },
    }
}

// --- Background task ---

#[allow(clippy::too_many_arguments)]
async fn background_task(
    kind: SdkKind,
    mut child: Child,
    mut stdin: ChildStdin,
    mut raw_event_rx: mpsc::UnboundedReceiver<serde_json::Value>,
    reader_handle: tokio::task::JoinHandle<()>,
    mut cmd_rx: mpsc::UnboundedReceiver<AgentCommand>,
    event_tx: mpsc::UnboundedSender<AgentEvent>,
    cwd: String,
    mcp_servers: serde_json::Value,
    parsed_config: ParsedConfig,
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
                                        let cmd = SdkCommand::Prompt { text };
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
                                    // Text deltas have content blocks with type "text_delta" — these are
                                    // partial fragments that should not overwrite the final complete message
                                    // and should not be persisted to the DB (ephemeral streaming only).
                                    let is_delta_only = content.as_array()
                                        .map(|blocks| !blocks.is_empty() && blocks.iter().all(|b|
                                            b.get("type").and_then(|t| t.as_str()) == Some("text_delta")
                                        ))
                                        .unwrap_or(false);
                                    let _ = event_tx.send(AgentEvent::Message { output: structured.clone(), ephemeral: is_delta_only });
                                    if !is_delta_only {
                                        last_content = Some(structured);
                                    }
                                }
                            }
                            "result" => match serde_json::from_value::<ResultEvent>(event) {
                                Ok(result) => {
                                    if let Some(ref sid) = result.session_id {
                                        agent_session_id = Some(sid.clone());
                                    }
                                    let mut turn = map_result_to_turn(
                                        kind,
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
                            error: format!("{} executor process died ({exit_info})", kind.label()),
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
                                let resume_session_id = task_payload
                                    .get("resumeSessionId")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string());
                                let cmd = build_start_command(
                                    prompt_text,
                                    &cwd,
                                    &mcp_servers,
                                    &parsed_config,
                                    resume_session_id,
                                );
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
                            // Buffer until agent is initialized
                            pending_prompts.push(text);
                        } else {
                            let cmd = SdkCommand::Prompt { text };
                            if let Err(e) = write_command(&mut stdin, &cmd).await {
                                tracing::warn!(error = %e, "failed to write prompt command");
                            }
                        }
                    }
                    Some(AgentCommand::Cancel) => {
                        let _ = write_command(&mut stdin, &SdkCommand::Cancel).await;
                    }
                    Some(AgentCommand::Stop) => {
                        let _ = write_command(&mut stdin, &SdkCommand::Stop).await;
                        // Close stdin to signal EOF
                        drop(stdin);
                        // Wait for process with timeout
                        let timeout = std::time::Duration::from_secs(5);
                        match tokio::time::timeout(timeout, child.wait()).await {
                            Ok(Ok(_)) => {}
                            Ok(Err(e)) => {
                                tracing::warn!(error = %e, "error waiting for {} executor to exit", kind.label());
                            }
                            Err(_) => {
                                tracing::warn!("{} executor did not exit within timeout, killing", kind.label());
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

async fn write_command(stdin: &mut ChildStdin, cmd: &SdkCommand) -> Result<()> {
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

/// Map SDK result subtypes to TurnResult with ErrorKind.
fn map_result_to_turn(
    kind: SdkKind,
    result: ResultEvent,
    agent_session_id: Option<String>,
    last_content: Option<serde_json::Value>,
) -> TurnResult {
    let (error, error_kind) = match result.subtype.as_str() {
        "success" => (None, None),
        "cancelled" => (Some("session cancelled".into()), Some(ErrorKind::Cancelled)),
        "error_max_turns" if kind == SdkKind::Claude => (
            Some(
                result
                    .errors
                    .as_ref()
                    .map_or("max turns reached".into(), |e| e.join("; ")),
            ),
            Some(ErrorKind::MaxTurns),
        ),
        "error_max_budget_usd" if kind == SdkKind::Claude => (
            Some(
                result
                    .errors
                    .as_ref()
                    .map_or("budget exceeded".into(), |e| e.join("; ")),
            ),
            Some(ErrorKind::BudgetExceeded),
        ),
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

    // --- Init event parsing ---

    #[test]
    fn test_parse_init_event() {
        let json_str = r#"{"type":"init","sessionId":"abc-123","mcpServers":[{"name":"agentbeacon","status":"connected"}]}"#;
        let value: serde_json::Value = serde_json::from_str(json_str).unwrap();
        let init: InitEvent = serde_json::from_value(value).unwrap();
        assert_eq!(init.session_id, "abc-123");
    }

    // --- Result mapping: Claude-specific ---

    #[test]
    fn test_claude_result_success() {
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
        let turn = map_result_to_turn(SdkKind::Claude, result, Some("abc-123".into()), None);
        assert!(turn.error.is_none());
        assert!(turn.error_kind.is_none());
        assert!(turn.output.is_some());
    }

    #[test]
    fn test_claude_result_max_turns() {
        let value = json!({
            "type": "result",
            "subtype": "error_max_turns",
            "sessionId": "abc-123",
            "errors": ["Hit 50 turn limit"],
            "costUsd": 2.10,
            "numTurns": 50
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(SdkKind::Claude, result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::MaxTurns));
    }

    #[test]
    fn test_claude_result_max_budget() {
        let value = json!({
            "type": "result",
            "subtype": "error_max_budget_usd",
            "sessionId": "abc-123",
            "errors": ["Budget exceeded"],
            "costUsd": 5.0,
            "numTurns": 12
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(SdkKind::Claude, result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::BudgetExceeded));
    }

    // --- Result mapping: shared ---

    #[test]
    fn test_result_cancelled() {
        let value = json!({
            "type": "result",
            "subtype": "cancelled",
            "sessionId": "abc-123",
            "costUsd": 0
        });
        let result: ResultEvent = serde_json::from_value(value).unwrap();
        let turn = map_result_to_turn(SdkKind::Claude, result, Some("abc-123".into()), None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::Cancelled));
    }

    // --- Result mapping: Copilot-specific ---

    #[test]
    fn test_copilot_result_error() {
        let result = ResultEvent {
            subtype: "error_during_execution".into(),
            session_id: None,
            result: None,
            errors: Some(vec!["sendAndWait returned undefined".into()]),
            cost_usd: None,
            num_turns: None,
            duration_ms: None,
        };
        let turn = map_result_to_turn(SdkKind::Copilot, result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::ExecutorFailed));
    }

    #[test]
    fn test_copilot_result_no_cost() {
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
    fn test_copilot_error_max_turns_falls_to_catch_all() {
        // Copilot SDK doesn't emit error_max_turns — should fall through to generic catch-all
        let result = ResultEvent {
            subtype: "error_max_turns".into(),
            session_id: None,
            result: None,
            errors: Some(vec!["max turns".into()]),
            cost_usd: None,
            num_turns: None,
            duration_ms: None,
        };
        let turn = map_result_to_turn(SdkKind::Copilot, result, None, None);
        assert!(turn.error.is_some());
        assert_eq!(turn.error_kind, Some(ErrorKind::ExecutorFailed));
    }

    // --- Event type filtering ---

    #[test]
    fn test_unknown_event_type_filtered() {
        let value = json!({"type": "stream_event", "data": "partial"});
        let type_field = value.get("type").and_then(|t| t.as_str()).unwrap();
        let known = matches!(type_field, "init" | "message" | "result" | "error");
        assert!(!known);
    }

    #[test]
    fn test_non_json_line_filtered() {
        let result = serde_json::from_str::<serde_json::Value>("not json at all");
        assert!(result.is_err());
    }

    // --- Command serialization: Claude ---

    #[test]
    fn test_claude_start_command() {
        let cmd = SdkCommand::Start {
            prompt: "Hello".into(),
            cwd: "/workspace".into(),
            mcp_servers: Some(
                json!({"agentbeacon": {"type": "http", "url": "http://localhost:9456/mcp"}}),
            ),
            model: Some("claude-sonnet-4-5-20250929".into()),
            max_turns: Some(50),
            max_budget_usd: Some(5.0),
            system_prompt: None,
            resume_session_id: None,
            provider: None,
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "start");
        assert_eq!(value["prompt"], "Hello");
        assert_eq!(value["cwd"], "/workspace");
        assert_eq!(value["maxTurns"], 50);
        assert_eq!(value["maxBudgetUsd"], 5.0);
        assert!(value.get("systemPrompt").is_none());
        assert!(value.get("resumeSessionId").is_none());
        assert!(value.get("provider").is_none());
    }

    // --- Command serialization: Copilot ---

    #[test]
    fn test_copilot_start_command() {
        let cmd = SdkCommand::Start {
            prompt: "Fix the tests".into(),
            cwd: "/workspace".into(),
            mcp_servers: Some(
                json!({"agentbeacon": {"type": "http", "url": "http://localhost:9456/mcp"}}),
            ),
            model: Some("gpt-5".into()),
            system_prompt: None,
            resume_session_id: None,
            max_turns: None,
            max_budget_usd: None,
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
        assert!(value.get("resumeSessionId").is_none());
        assert!(value.get("maxTurns").is_none());
        assert!(value.get("maxBudgetUsd").is_none());
    }

    #[test]
    fn test_copilot_start_command_provider_not_in_claude() {
        // When building a Claude start command, provider should be None
        let config = ParsedConfig::Claude(ClaudeConfig {
            model: Some("claude-sonnet-4-5-20250929".into()),
            max_turns: Some(10),
            max_budget_usd: None,
            system_prompt: None,
        });
        let mcp = json!({"agentbeacon": {"type": "http", "url": "http://localhost:9456/mcp"}});
        let cmd = build_start_command("hello".into(), "/workspace", &mcp, &config, None);
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert!(value.get("provider").is_none());
        assert_eq!(value["maxTurns"], 10);
    }

    // --- Resume session ID ---

    #[test]
    fn test_start_command_resume() {
        let cmd = SdkCommand::Start {
            prompt: "Resume task".into(),
            cwd: "/workspace".into(),
            mcp_servers: None,
            model: None,
            max_turns: None,
            max_budget_usd: None,
            system_prompt: None,
            resume_session_id: Some("sdk-session-abc".into()),
            provider: None,
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "start");
        assert_eq!(value["resumeSessionId"], "sdk-session-abc");
    }

    #[test]
    fn test_start_command_no_resume() {
        let cmd = SdkCommand::Start {
            prompt: "Hello".into(),
            cwd: "/workspace".into(),
            mcp_servers: None,
            model: None,
            max_turns: None,
            max_budget_usd: None,
            system_prompt: None,
            resume_session_id: None,
            provider: None,
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert!(value.get("resumeSessionId").is_none());
    }

    // --- Prompt command ---

    #[test]
    fn test_prompt_command() {
        let cmd = SdkCommand::Prompt {
            text: "continue with JWT".into(),
        };
        let json_str = serde_json::to_string(&cmd).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(value["type"], "prompt");
        assert_eq!(value["text"], "continue with JWT");
    }

    // --- Config parsing ---

    #[test]
    fn test_claude_config_parsing() {
        let json_val = json!({
            "model": "claude-sonnet-4-5-20250929",
            "max_turns": 50,
            "max_budget_usd": 5.0,
            "system_prompt": "You are an expert."
        });
        let config: ClaudeConfig = serde_json::from_value(json_val).unwrap();
        assert_eq!(config.model.as_deref(), Some("claude-sonnet-4-5-20250929"));
        assert_eq!(config.max_turns, Some(50));
        assert_eq!(config.max_budget_usd, Some(5.0));
        assert_eq!(config.system_prompt.as_deref(), Some("You are an expert."));
    }

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

    // --- sanitize_provider ---

    #[test]
    fn test_sanitize_provider_strips_sensitive_keys() {
        let provider = json!({
            "type": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-secret123",
            "bearer_token": "tok-secret"
        });
        let sanitized = sanitize_provider(Some(provider)).unwrap();
        assert_eq!(sanitized["type"], "openai");
        assert_eq!(sanitized["baseUrl"], "https://api.openai.com/v1");
        assert!(sanitized.get("apiKey").is_none());
        assert!(sanitized.get("bearer_token").is_none());
    }

    #[test]
    fn test_sanitize_provider_none_passthrough() {
        assert!(sanitize_provider(None).is_none());
    }
}
