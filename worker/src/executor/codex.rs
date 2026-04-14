//! Codex executor adapter — drives the Codex `app-server` binary via
//! bidirectional JSON-RPC 2.0 over stdio.
//!
//! Background task pattern: `start()` parses config, prepares CODEX_HOME,
//! spawns the process, performs the initialize handshake and thread creation,
//! then enters a `tokio::select!`-based event loop bridging AgentCommand/AgentEvent
//! channels with the subprocess stdin/stdout.

use anyhow::{Context, Result};
use base64::Engine;
use common::a2a::Part;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::mpsc;
use tokio::time::Instant;

use super::{
    AgentCommand, AgentEvent, ErrorKind, ExecutorHandle, SessionConfig, StderrBuffer, TurnResult,
    extract_parts, new_stderr_buffer, push_stderr_line, snapshot_stderr,
};
use crate::embedded_executors::resolve_data_dir;

const INTERRUPT_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_SIGINT_TIMEOUT: Duration = Duration::from_secs(3);

/// Ephemeral notification methods — forwarded as `AgentEvent::Message { ephemeral: true }`.
const EPHEMERAL_METHODS: &[&str] = &[
    "item/agentMessage/delta",
    "item/reasoning/textDelta",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/summaryTextDelta",
    "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta",
    "item/plan/delta",
    "mcpToolCall/progress",
    "item/started",
    "turn/started",
];

#[derive(Debug, Deserialize)]
pub struct CodexConfig {
    #[serde(default = "default_command")]
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub timeout: Option<u64>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    pub model: Option<String>,
    pub system_prompt: Option<String>,
    #[serde(default = "default_approval_policy")]
    pub approval_policy: String,
    #[serde(default = "default_sandbox_policy")]
    pub sandbox_policy: String,
    #[serde(default)]
    pub persist_extended_history: Option<bool>,
}

fn default_command() -> String {
    "codex".to_string()
}
fn default_approval_policy() -> String {
    "never".to_string()
}
fn default_sandbox_policy() -> String {
    "danger-full-access".to_string()
}

/// Monotonically increasing request ID generator.
static NEXT_ID: AtomicU64 = AtomicU64::new(0);

fn next_request_id() -> u64 {
    NEXT_ID.fetch_add(1, Ordering::Relaxed)
}

/// Write a JSON-RPC request (with id) and return the id.
async fn send_request(
    stdin: &mut tokio::process::ChildStdin,
    method: &str,
    params: serde_json::Value,
) -> Result<u64> {
    let id = next_request_id();
    let msg = serde_json::json!({
        "method": method,
        "id": id,
        "params": params,
    });
    let mut line = serde_json::to_string(&msg)?;
    line.push('\n');
    stdin.write_all(line.as_bytes()).await?;
    stdin.flush().await?;
    Ok(id)
}

/// Write a JSON-RPC notification (no id).
async fn send_notification(stdin: &mut tokio::process::ChildStdin, method: &str) -> Result<()> {
    let msg = serde_json::json!({ "method": method });
    let mut line = serde_json::to_string(&msg)?;
    line.push('\n');
    stdin.write_all(line.as_bytes()).await?;
    stdin.flush().await?;
    Ok(())
}

/// Send a JSON-RPC response (for server-initiated requests).
async fn send_response(
    stdin: &mut tokio::process::ChildStdin,
    id: &serde_json::Value,
    result: serde_json::Value,
) -> Result<()> {
    let msg = serde_json::json!({
        "id": id,
        "result": result,
    });
    let mut line = serde_json::to_string(&msg)?;
    line.push('\n');
    stdin.write_all(line.as_bytes()).await?;
    stdin.flush().await?;
    Ok(())
}

/// Send a JSON-RPC error response.
async fn send_error_response(
    stdin: &mut tokio::process::ChildStdin,
    id: &serde_json::Value,
    code: i64,
    message: &str,
) -> Result<()> {
    let msg = serde_json::json!({
        "id": id,
        "error": { "code": code, "message": message },
    });
    let mut line = serde_json::to_string(&msg)?;
    line.push('\n');
    stdin.write_all(line.as_bytes()).await?;
    stdin.flush().await?;
    Ok(())
}

/// Wait for a response with a given id, draining and forwarding notifications.
/// Returns the full JSON response object (with `result` or `error`).
async fn wait_for_response(
    id: u64,
    lines: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    notification_sink: &mpsc::UnboundedSender<serde_json::Value>,
) -> Result<serde_json::Value> {
    loop {
        let line = lines
            .next_line()
            .await?
            .ok_or_else(|| anyhow::anyhow!("process stdout closed while awaiting response"))?;
        if line.trim().is_empty() {
            continue;
        }
        let msg: serde_json::Value = serde_json::from_str(&line).with_context(|| {
            format!(
                "malformed JSON from codex: {}",
                &line[..line.len().min(120)]
            )
        })?;

        if let Some(resp_id) = msg.get("id")
            && resp_id.as_u64() == Some(id)
            && msg.get("method").is_none()
        {
            return Ok(msg);
        }
        let _ = notification_sink.send(msg);
    }
}

/// Decode image parts from A2A format to temp files, returning Codex input items.
/// Non-image parts with `raw` set are skipped (not supported in MVP).
fn parts_to_codex_input(
    parts: &[serde_json::Value],
) -> Result<(Vec<serde_json::Value>, Vec<PathBuf>)> {
    let mut input = Vec::new();
    let mut temp_files = Vec::new();

    for part_val in parts {
        let part: Part = serde_json::from_value(part_val.clone()).unwrap_or_default();

        if let Some(ref text) = part.text {
            input.push(serde_json::json!({ "type": "text", "text": text }));
        } else if let Some(ref raw_b64) = part.raw {
            let media = part
                .media_type
                .as_deref()
                .unwrap_or("application/octet-stream");
            if media.starts_with("image/") {
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(raw_b64)
                    .with_context(|| "failed to decode base64 image data")?;
                let ext = match media {
                    "image/png" => "png",
                    "image/jpeg" | "image/jpg" => "jpg",
                    "image/gif" => "gif",
                    "image/webp" => "webp",
                    _ => "bin",
                };
                let filename = format!("ab-{}.{}", uuid::Uuid::new_v4(), ext);
                let path = std::env::temp_dir().join(&filename);
                std::fs::write(&path, &bytes)
                    .with_context(|| format!("failed to write temp image: {}", path.display()))?;
                input.push(serde_json::json!({
                    "type": "localImage",
                    "path": path.to_string_lossy(),
                }));
                temp_files.push(path);
            } else {
                tracing::debug!(media_type = %media, "skipping non-image raw part (unsupported in MVP)");
            }
        }
    }

    if input.is_empty() {
        input.push(serde_json::json!({ "type": "text", "text": "" }));
    }

    Ok((input, temp_files))
}

/// Remove temp files from a list of paths.
fn cleanup_temp_files(files: &[PathBuf]) {
    for path in files {
        if let Err(e) = std::fs::remove_file(path) {
            tracing::debug!(path = %path.display(), error = %e, "failed to clean up temp image");
        }
    }
}

fn write_config_toml(
    codex_home: &Path,
    config: &CodexConfig,
    scheduler_url: &str,
    user_mcp_servers: &serde_json::Value,
) -> Result<()> {
    use toml::Value as Tv;

    let mut root = toml::map::Map::new();

    if let Some(ref model) = config.model {
        root.insert("model".into(), Tv::String(model.clone()));
    }

    root.insert(
        "sandbox_mode".into(),
        Tv::String(config.sandbox_policy.clone()),
    );

    root.insert("approval_policy".into(), Tv::String("never".into()));

    if let Some(ref sp) = config.system_prompt {
        root.insert("developer_instructions".into(), Tv::String(sp.clone()));
    }

    if let Some(persist) = config.persist_extended_history {
        root.insert("persist_extended_history".into(), Tv::Boolean(persist));
    }

    let mut mcp_table = toml::map::Map::new();

    let mcp_url = format!("{}/mcp", scheduler_url.trim_end_matches('/'));
    let mut ab_server = toml::map::Map::new();
    ab_server.insert("url".into(), Tv::String(mcp_url));
    ab_server.insert(
        "bearer_token_env_var".into(),
        Tv::String("AGENTBEACON_SESSION_ID".into()),
    );
    ab_server.insert("required".into(), Tv::Boolean(true));
    mcp_table.insert("agentbeacon".into(), Tv::Table(ab_server));

    if let serde_json::Value::Object(servers) = user_mcp_servers {
        for (name, srv_config) in servers {
            if name == "agentbeacon" {
                continue;
            }
            let srv_type = srv_config
                .get("type")
                .and_then(|t| t.as_str())
                .unwrap_or("");

            let mut srv_table = toml::map::Map::new();

            match srv_type {
                "http" => {
                    if let Some(url) = srv_config.get("url").and_then(|u| u.as_str()) {
                        srv_table.insert("url".into(), Tv::String(url.to_string()));
                    }
                    if let Some(headers) = srv_config.get("headers").and_then(|h| h.as_object()) {
                        let mut h_table = toml::map::Map::new();
                        for (k, v) in headers {
                            if let Some(vs) = v.as_str() {
                                h_table.insert(k.clone(), Tv::String(vs.to_string()));
                            }
                        }
                        if !h_table.is_empty() {
                            srv_table.insert("http_headers".into(), Tv::Table(h_table));
                        }
                    }
                }
                "stdio" | "local" => {
                    if let Some(cmd) = srv_config.get("command").and_then(|c| c.as_str()) {
                        srv_table.insert("command".into(), Tv::String(cmd.to_string()));
                    }
                    if let Some(args) = srv_config.get("args").and_then(|a| a.as_array()) {
                        let arr: Vec<Tv> = args
                            .iter()
                            .filter_map(|a| a.as_str().map(|s| Tv::String(s.to_string())))
                            .collect();
                        srv_table.insert("args".into(), Tv::Array(arr));
                    }
                    if let Some(env) = srv_config.get("env").and_then(|e| e.as_object()) {
                        let mut e_table = toml::map::Map::new();
                        for (k, v) in env {
                            if let Some(vs) = v.as_str() {
                                e_table.insert(k.clone(), Tv::String(vs.to_string()));
                            }
                        }
                        if !e_table.is_empty() {
                            srv_table.insert("env".into(), Tv::Table(e_table));
                        }
                    }
                    if let Some(cwd) = srv_config.get("cwd").and_then(|c| c.as_str()) {
                        srv_table.insert("cwd".into(), Tv::String(cwd.to_string()));
                    }
                }
                other => {
                    tracing::warn!(
                        server = %name,
                        transport = %other,
                        "skipping MCP server with unknown transport type"
                    );
                    continue;
                }
            }

            mcp_table.insert(name.clone(), Tv::Table(srv_table));
        }
    }

    root.insert("mcp_servers".into(), Tv::Table(mcp_table));

    let mut features = toml::map::Map::new();
    features.insert("multi_agent".into(), Tv::Boolean(false));
    root.insert("features".into(), Tv::Table(features));

    let toml_str =
        toml::to_string_pretty(&Tv::Table(root)).context("failed to serialize config.toml")?;

    let config_path = codex_home.join("config.toml");
    std::fs::create_dir_all(codex_home)
        .with_context(|| format!("failed to create CODEX_HOME: {}", codex_home.display()))?;
    std::fs::write(&config_path, toml_str)
        .with_context(|| format!("failed to write config.toml: {}", config_path.display()))?;

    Ok(())
}

fn derive_codex_home(session_id: &str, resume_agent_session_id: Option<&str>) -> PathBuf {
    let data_dir = resolve_data_dir();
    let codex_state = data_dir.join("codex-state");

    match resume_agent_session_id {
        Some(agent_sid) => codex_state.join(agent_sid).join(".codex"),
        None => codex_state.join(session_id).join(".codex"),
    }
}

/// After thread/start returns a thread_id for a fresh session, create a symlink
/// so that resume (which uses thread_id as agent_session_id) can find the CODEX_HOME.
fn create_thread_symlink(session_id: &str, thread_id: &str) {
    let data_dir = resolve_data_dir();
    let codex_state = data_dir.join("codex-state");
    let link_path = codex_state.join(thread_id);
    let target = codex_state.join(session_id);

    if link_path.exists() || link_path.symlink_metadata().is_ok() {
        return;
    }
    if let Err(e) = std::os::unix::fs::symlink(&target, &link_path) {
        tracing::warn!(
            thread_id = %thread_id,
            session_id = %session_id,
            error = %e,
            "failed to create thread_id symlink for CODEX_HOME"
        );
    }
}

pub async fn start(config: SessionConfig) -> Result<ExecutorHandle> {
    let codex_config: CodexConfig = serde_json::from_value(config.agent_config.clone())
        .context("failed to parse Codex agent config")?;

    if !codex_config.approval_policy.is_empty() && codex_config.approval_policy != "never" {
        tracing::warn!(
            "Codex approval_policy is {:?} but AgentBeacon is headless — non-never policies are not supported. \
             The executor will use approvalPolicy: \"never\" on thread/start (RPC params take precedence) \
             and auto-accept any approval requests as a safety net.",
            codex_config.approval_policy
        );
    }

    let init_timeout = Duration::from_secs(codex_config.timeout.unwrap_or(30));

    let codex_home = derive_codex_home(
        &config.session_id,
        config.resume_agent_session_id.as_deref(),
    );

    write_config_toml(
        &codex_home,
        &codex_config,
        &config.scheduler_url,
        &config.user_mcp_servers,
    )?;

    let mut args = codex_config.args.clone();
    if args.is_empty() && codex_config.command == "codex" {
        args = vec![
            "app-server".to_string(),
            "--listen".to_string(),
            "stdio://".to_string(),
        ];
    }

    let mut cmd = tokio::process::Command::new(&codex_config.command);
    cmd.args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);

    for (k, v) in &codex_config.env {
        cmd.env(k, v);
    }

    cmd.env("CODEX_HOME", codex_home.to_string_lossy().as_ref());
    cmd.env("AGENTBEACON_SESSION_ID", &config.session_id);
    cmd.env("AGENTBEACON_API_BASE", &config.scheduler_url);
    cmd.env("AGENTBEACON_EXECUTION_ID", &config.execution_id);
    if let Some(ref pid) = config.project_id {
        cmd.env("AGENTBEACON_PROJECT_ID", pid);
    } else {
        cmd.env("AGENTBEACON_PROJECT_ID", "");
    }

    let mut child = cmd.spawn().with_context(|| {
        format!(
            "failed to spawn Codex executor: {} {}",
            codex_config.command,
            args.join(" ")
        )
    })?;

    let child_pid = child.id();
    let stdin = child.stdin.take().context("failed to get Codex stdin")?;
    let stdout = child.stdout.take().context("failed to get Codex stdout")?;
    let stderr = child.stderr.take().context("failed to get Codex stderr")?;

    let stderr_buf = new_stderr_buffer();
    let buf_clone = stderr_buf.clone();
    tokio::spawn(async move {
        let reader = BufReader::new(stderr);
        let mut lines = reader.lines();
        while let Ok(Some(line)) = lines.next_line().await {
            tracing::debug!(target: "codex_executor", "{}", line);
            push_stderr_line(&buf_clone, line);
        }
    });

    let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();
    let (event_tx, event_rx) = mpsc::unbounded_channel();

    let task_handle = tokio::spawn(background_task(
        child,
        stdin,
        stdout,
        cmd_rx,
        event_tx,
        config.session_id,
        config.execution_id,
        config.cwd,
        codex_config,
        stderr_buf,
        config.inactivity_timeout,
        init_timeout,
    ));

    Ok(ExecutorHandle {
        cmd_tx,
        event_rx,
        task_handle,
        child_pid,
    })
}

struct TurnState {
    thread_id: Option<String>,
    active_turn_id: Option<String>,
    stop_turn_requested: bool,
    deferred_prompts: Vec<(Vec<serde_json::Value>, Vec<PathBuf>)>,
    fallback_in_flight: bool,
    /// True after we have emitted Init for this executor lifetime.
    init_emitted: bool,
    /// Tracks whether the cancel command was received.
    cancel_requested: bool,
    /// Temp files from the currently active turn's direct delivery.
    active_turn_temp_files: Vec<PathBuf>,
    /// Pending fallback input: set by handle_turn_completed when deferred prompts
    /// need to be delivered via a new turn/start. The event loop picks this up.
    pending_fallback_input: Option<Vec<serde_json::Value>>,
}

impl TurnState {
    fn new() -> Self {
        Self {
            thread_id: None,
            active_turn_id: None,
            stop_turn_requested: false,
            deferred_prompts: Vec::new(),
            fallback_in_flight: false,
            init_emitted: false,
            pending_fallback_input: None,
            cancel_requested: false,
            active_turn_temp_files: Vec::new(),
        }
    }

    fn settled(&self) -> bool {
        !self.fallback_in_flight && self.deferred_prompts.is_empty()
    }
}

#[allow(clippy::too_many_arguments)]
async fn background_task(
    mut child: tokio::process::Child,
    stdin: tokio::process::ChildStdin,
    stdout: tokio::process::ChildStdout,
    mut cmd_rx: mpsc::UnboundedReceiver<AgentCommand>,
    event_tx: mpsc::UnboundedSender<AgentEvent>,
    session_id: String,
    execution_id: String,
    cwd: String,
    codex_config: CodexConfig,
    stderr_buf: StderrBuffer,
    inactivity_timeout: Duration,
    init_timeout: Duration,
) {
    let mut stdin = Some(stdin);

    let mut stdout_reader = BufReader::new(stdout).lines();
    let (notif_tx, mut notif_rx) = mpsc::unbounded_channel::<serde_json::Value>();

    let init_result = async {
        let id = send_request(
            stdin.as_mut().unwrap(),
            "initialize",
            serde_json::json!({
                "clientInfo": { "name": "agentbeacon_worker", "version": "0.1.0" },
                "capabilities": { "experimentalApi": true },
            }),
        )
        .await?;

        let resp = tokio::time::timeout(
            init_timeout,
            wait_for_response(id, &mut stdout_reader, &notif_tx),
        )
        .await
        .context("initialize timed out")?
        .context("initialize failed")?;

        if resp.get("error").is_some() {
            anyhow::bail!(
                "initialize rejected: {}",
                serde_json::to_string(&resp).unwrap_or_default()
            );
        }

        if let Some(result) = resp.get("result") {
            check_server_version(result)?;
        }

        send_notification(stdin.as_mut().unwrap(), "initialized").await?;

        Ok::<_, anyhow::Error>(())
    }
    .await;

    if let Err(e) = init_result {
        let _ = child.kill().await;
        let _ = event_tx.send(AgentEvent::ProcessDied {
            error: format!("Codex initialize failed: {e}"),
            stderr: snapshot_stderr(&stderr_buf),
            agent_session_id: None,
        });
        return;
    }

    tracing::info!(
        execution_id = %execution_id,
        "Codex initialized, starting background event loop"
    );

    let mut state = TurnState::new();
    let mut last_activity = Instant::now();

    loop {
        if let Some(fallback_input) = state.pending_fallback_input.take()
            && let Some(ref thread_id) = state.thread_id
        {
            let tid = thread_id.clone();
            match send_request(
                stdin.as_mut().unwrap(),
                "turn/start",
                serde_json::json!({
                    "threadId": tid,
                    "input": fallback_input,
                }),
            )
            .await
            {
                Ok(id) => {
                    match tokio::time::timeout(
                        init_timeout,
                        wait_for_response(id, &mut stdout_reader, &notif_tx),
                    )
                    .await
                    {
                        Ok(Ok(resp)) => {
                            if resp.get("error").is_some() {
                                let _ = child.kill().await;
                                let _ = event_tx.send(AgentEvent::ProcessDied {
                                    error: format!(
                                        "fallback turn/start rejected: {}",
                                        serde_json::to_string(&resp).unwrap_or_default()
                                    ),
                                    stderr: snapshot_stderr(&stderr_buf),
                                    agent_session_id: state.thread_id.clone(),
                                });
                                cleanup_state_temp_files(&mut state);
                                return;
                            }
                            if let Some(turn_id) = resp
                                .get("result")
                                .and_then(|r| r.get("turn"))
                                .and_then(|t| t.get("id"))
                                .and_then(|i| i.as_str())
                            {
                                state.active_turn_id = Some(turn_id.to_string());
                                state.fallback_in_flight = false;
                                last_activity = Instant::now();

                                if state.stop_turn_requested
                                    && let Some(ref tid) = state.thread_id
                                {
                                    let _ = send_request(
                                        stdin.as_mut().unwrap(),
                                        "turn/interrupt",
                                        serde_json::json!({
                                            "threadId": tid,
                                            "turnId": turn_id,
                                        }),
                                    )
                                    .await;
                                }
                            }
                        }
                        Ok(Err(e)) => {
                            let _ = child.kill().await;
                            let _ = event_tx.send(AgentEvent::ProcessDied {
                                error: format!("fallback turn/start failed: {e}"),
                                stderr: snapshot_stderr(&stderr_buf),
                                agent_session_id: state.thread_id.clone(),
                            });
                            cleanup_state_temp_files(&mut state);
                            return;
                        }
                        Err(_elapsed) => {
                            let _ = child.kill().await;
                            let _ = event_tx.send(AgentEvent::ProcessDied {
                                error: "fallback turn/start timed out".to_string(),
                                stderr: snapshot_stderr(&stderr_buf),
                                agent_session_id: state.thread_id.clone(),
                            });
                            cleanup_state_temp_files(&mut state);
                            return;
                        }
                    }
                }
                Err(e) => {
                    let _ = child.kill().await;
                    let _ = event_tx.send(AgentEvent::ProcessDied {
                        error: format!("failed to send fallback turn/start: {e}"),
                        stderr: snapshot_stderr(&stderr_buf),
                        agent_session_id: state.thread_id.clone(),
                    });
                    cleanup_state_temp_files(&mut state);
                    return;
                }
            }
        }

        tokio::select! {
            biased;

            notif = notif_rx.recv() => {
                match notif {
                    Some(msg) => {
                        last_activity = Instant::now();
                        handle_incoming_message(
                            &msg, &mut state, &event_tx, stdin.as_mut().unwrap(),
                            &session_id, &stderr_buf,
                        ).await;
                    }
                    None => {
                        break;
                    }
                }
            }

            line_result = stdout_reader.next_line() => {
                match line_result {
                    Ok(Some(line)) => {
                        if line.trim().is_empty() {
                            continue;
                        }
                        last_activity = Instant::now();
                        match serde_json::from_str::<serde_json::Value>(&line) {
                            Ok(msg) => {
                                handle_incoming_message(
                                    &msg, &mut state, &event_tx, stdin.as_mut().unwrap(),
                                    &session_id, &stderr_buf,
                                ).await;
                            }
                            Err(e) => {
                                tracing::warn!(error = %e, "malformed JSON from Codex stdout");
                            }
                        }
                    }
                    Ok(None) => {
                        let exit_info = match child.try_wait() {
                            Ok(Some(status)) => format!("exit code: {status}"),
                            Ok(None) => "still running".to_string(),
                            Err(e) => format!("error checking status: {e}"),
                        };
                        let _ = event_tx.send(AgentEvent::ProcessDied {
                            error: format!("Codex process died ({exit_info})"),
                            stderr: snapshot_stderr(&stderr_buf),
                            agent_session_id: state.thread_id.clone(),
                        });
                        cleanup_state_temp_files(&mut state);
                        return;
                    }
                    Err(e) => {
                        let _ = event_tx.send(AgentEvent::ProcessDied {
                            error: format!("Codex stdout read error: {e}"),
                            stderr: snapshot_stderr(&stderr_buf),
                            agent_session_id: state.thread_id.clone(),
                        });
                        cleanup_state_temp_files(&mut state);
                        let _ = child.kill().await;
                        return;
                    }
                }
            }

            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(AgentCommand::Start(task_payload)) => {
                        let resume_session_id = task_payload
                            .get("resumeSessionId")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());

                        let parts = match extract_parts(&task_payload) {
                            Ok(p) => p,
                            Err(e) => {
                                let _ = child.kill().await;
                                let _ = event_tx.send(AgentEvent::ProcessDied {
                                    error: format!("bad task payload: {e}"),
                                    stderr: snapshot_stderr(&stderr_buf),
                                    agent_session_id: state.thread_id.clone(),
                                });
                                return;
                            }
                        };

                        let (input, temp_files) = match parts_to_codex_input(&parts) {
                            Ok(r) => r,
                            Err(e) => {
                                let _ = child.kill().await;
                                let _ = event_tx.send(AgentEvent::ProcessDied {
                                    error: format!("failed to prepare input: {e}"),
                                    stderr: snapshot_stderr(&stderr_buf),
                                    agent_session_id: state.thread_id.clone(),
                                });
                                return;
                            }
                        };

                        if let Some(ref resume_id) = resume_session_id {
                            if let Err(e) = do_resume(
                                stdin.as_mut().unwrap(), &mut stdout_reader, &notif_tx,
                                &mut state, resume_id, &input,
                                init_timeout,
                            ).await {
                                let _ = child.kill().await;
                                let _ = event_tx.send(AgentEvent::ProcessDied {
                                    error: format!("Codex resume failed: {e}"),
                                    stderr: snapshot_stderr(&stderr_buf),
                                    agent_session_id: state.thread_id.clone(),
                                });
                                cleanup_temp_files(&temp_files);
                                return;
                            }
                        } else {
                            if let Err(e) = do_fresh_start(
                                stdin.as_mut().unwrap(), &mut stdout_reader, &notif_tx,
                                &mut state, &session_id, &cwd,
                                &codex_config, &input, init_timeout,
                            ).await {
                                let _ = child.kill().await;
                                let _ = event_tx.send(AgentEvent::ProcessDied {
                                    error: format!("Codex start failed: {e}"),
                                    stderr: snapshot_stderr(&stderr_buf),
                                    agent_session_id: state.thread_id.clone(),
                                });
                                cleanup_temp_files(&temp_files);
                                return;
                            }
                        }

                        if !state.init_emitted
                            && let Some(ref tid) = state.thread_id
                        {
                            let _ = event_tx.send(AgentEvent::Init {
                                session_id: tid.clone(),
                            });
                            state.init_emitted = true;
                        }
                        state.active_turn_temp_files = temp_files;
                    }

                    Some(AgentCommand::Prompt(a2a_parts)) => {
                        handle_prompt(
                            &a2a_parts, stdin.as_mut().unwrap(), &mut stdout_reader, &notif_tx,
                            &mut state, &event_tx, &session_id, &stderr_buf,
                            &mut child, init_timeout,
                        ).await;
                    }

                    Some(AgentCommand::Cancel) => {
                        state.cancel_requested = true;
                        state.deferred_prompts.clear();
                        cleanup_deferred_temp_files(&mut state);

                        if let (Some(tid), Some(turn_id)) =
                            (&state.thread_id, &state.active_turn_id)
                        {
                            let _ = send_request(stdin.as_mut().unwrap(), "turn/interrupt", serde_json::json!({
                                "threadId": tid,
                                "turnId": turn_id,
                            })).await;
                            let deadline = Instant::now() + INTERRUPT_TIMEOUT;
                            loop {
                                tokio::select! {
                                    biased;
                                    line_result = stdout_reader.next_line() => {
                                        match line_result {
                                            Ok(Some(line)) if !line.trim().is_empty() => {
                                                if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&line) {
                                                    if is_turn_completed(&msg) {
                                                        handle_turn_completed(&msg, &mut state, &event_tx, &stderr_buf);
                                                        break;
                                                    }
                                                    handle_incoming_message(
                                                        &msg, &mut state, &event_tx, stdin.as_mut().unwrap(),
                                                        &session_id, &stderr_buf,
                                                    ).await;
                                                }
                                            }
                                            Ok(None) | Err(_) => break,
                                            _ => {}
                                        }
                                    }
                                    _ = tokio::time::sleep_until(deadline) => {
                                        tracing::warn!("turn/interrupt timed out, escalating to shutdown");
                                        break;
                                    }
                                }
                            }
                        }

                        shutdown_process(&mut child, &mut stdin).await;
                        let _ = event_tx.send(AgentEvent::ProcessDied {
                            error: "cancelled".to_string(),
                            stderr: snapshot_stderr(&stderr_buf),
                            agent_session_id: state.thread_id.clone(),
                        });
                        cleanup_state_temp_files(&mut state);
                        return;
                    }

                    Some(AgentCommand::StopTurn) => {
                        state.stop_turn_requested = true;
                        if !state.deferred_prompts.is_empty() {
                            tracing::info!(
                                count = state.deferred_prompts.len(),
                                "StopTurn: discarding deferred prompts"
                            );
                            cleanup_deferred_temp_files(&mut state);
                            state.deferred_prompts.clear();
                        }

                        if let (Some(tid), Some(turn_id)) =
                            (&state.thread_id, &state.active_turn_id)
                        {
                            let _ = send_request(stdin.as_mut().unwrap(), "turn/interrupt", serde_json::json!({
                                "threadId": tid,
                                "turnId": turn_id,
                            })).await;
                        } else if state.thread_id.is_some() {
                            let deadline = Instant::now() + INTERRUPT_TIMEOUT;
                            loop {
                                tokio::select! {
                                    biased;
                                    line_result = stdout_reader.next_line() => {
                                        match line_result {
                                            Ok(Some(line)) if !line.trim().is_empty() => {
                                                if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&line) {
                                                    if msg.get("id").is_some()
                                                        && msg.get("method").is_none()
                                                        && msg.get("result").is_some()
                                                        && let Some(turn_id) = msg
                                                            .get("result")
                                                            .and_then(|r| r.get("turn"))
                                                            .and_then(|t| t.get("id"))
                                                            .and_then(|i| i.as_str())
                                                    {
                                                        state.active_turn_id = Some(turn_id.to_string());
                                                        if state.fallback_in_flight {
                                                            state.fallback_in_flight = false;
                                                        }
                                                        if let Some(ref tid) = state.thread_id {
                                                            let _ = send_request(stdin.as_mut().unwrap(), "turn/interrupt", serde_json::json!({
                                                                "threadId": tid,
                                                                "turnId": turn_id,
                                                            })).await;
                                                        }
                                                        break;
                                                    }
                                                    handle_incoming_message(
                                                        &msg, &mut state, &event_tx, stdin.as_mut().unwrap(),
                                                        &session_id, &stderr_buf,
                                                    ).await;
                                                }
                                            }
                                            Ok(None) | Err(_) => break,
                                            _ => {}
                                        }
                                    }
                                    _ = tokio::time::sleep_until(deadline) => {
                                        tracing::warn!("StopTurn: timed out waiting for turn/start, escalating");
                                        shutdown_process(&mut child, &mut stdin).await;
                                        let _ = event_tx.send(AgentEvent::ProcessDied {
                                            error: "StopTurn escalated: turn/start hung".to_string(),
                                            stderr: snapshot_stderr(&stderr_buf),
                                            agent_session_id: state.thread_id.clone(),
                                        });
                                        cleanup_state_temp_files(&mut state);
                                        return;
                                    }
                                }
                            }
                        }
                    }

                    Some(AgentCommand::Stop) => {
                        shutdown_process(&mut child, &mut stdin).await;
                        cleanup_state_temp_files(&mut state);
                        return;
                    }

                    None => {
                        shutdown_process(&mut child, &mut stdin).await;
                        cleanup_state_temp_files(&mut state);
                        return;
                    }
                }
            }

            _ = tokio::time::sleep_until(last_activity + inactivity_timeout),
                if state.active_turn_id.is_some() && !state.cancel_requested => {
                tracing::warn!(
                    "Codex executor stalled: no output for {}s",
                    inactivity_timeout.as_secs()
                );
                let _ = child.kill().await;
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: format!(
                        "executor stalled: no output for {}s",
                        inactivity_timeout.as_secs()
                    ),
                    stderr: snapshot_stderr(&stderr_buf),
                    agent_session_id: state.thread_id.clone(),
                });
                cleanup_state_temp_files(&mut state);
                return;
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn do_fresh_start(
    stdin: &mut tokio::process::ChildStdin,
    stdout_reader: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    notif_tx: &mpsc::UnboundedSender<serde_json::Value>,
    state: &mut TurnState,
    session_id: &str,
    cwd: &str,
    codex_config: &CodexConfig,
    input: &[serde_json::Value],
    timeout: Duration,
) -> Result<()> {
    let thread_params = serde_json::json!({
        "cwd": cwd,
        "approvalPolicy": "never",
        "sandbox": codex_config.sandbox_policy,
    });

    let id = send_request(stdin, "thread/start", thread_params).await?;
    let resp = tokio::time::timeout(timeout, wait_for_response(id, stdout_reader, notif_tx))
        .await
        .context("thread/start timed out")?
        .context("thread/start failed")?;

    if resp.get("error").is_some() {
        anyhow::bail!(
            "thread/start rejected: {}",
            serde_json::to_string(&resp).unwrap_or_default()
        );
    }

    let thread_id = resp
        .get("result")
        .and_then(|r| r.get("thread"))
        .and_then(|t| t.get("id"))
        .and_then(|i| i.as_str())
        .ok_or_else(|| anyhow::anyhow!("thread/start response missing thread.id"))?
        .to_string();

    state.thread_id = Some(thread_id.clone());

    create_thread_symlink(session_id, &thread_id);

    do_turn_start(stdin, stdout_reader, notif_tx, state, input, timeout).await?;

    Ok(())
}

async fn do_resume(
    stdin: &mut tokio::process::ChildStdin,
    stdout_reader: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    notif_tx: &mpsc::UnboundedSender<serde_json::Value>,
    state: &mut TurnState,
    resume_thread_id: &str,
    input: &[serde_json::Value],
    timeout: Duration,
) -> Result<()> {
    let id = send_request(
        stdin,
        "thread/resume",
        serde_json::json!({
            "threadId": resume_thread_id,
        }),
    )
    .await?;

    let resp = tokio::time::timeout(timeout, wait_for_response(id, stdout_reader, notif_tx))
        .await
        .context("thread/resume timed out")?
        .context("thread/resume failed")?;

    if resp.get("error").is_some() {
        anyhow::bail!(
            "thread/resume rejected: {}",
            serde_json::to_string(&resp).unwrap_or_default()
        );
    }

    state.thread_id = Some(resume_thread_id.to_string());

    do_turn_start(stdin, stdout_reader, notif_tx, state, input, timeout).await?;

    Ok(())
}

async fn do_turn_start(
    stdin: &mut tokio::process::ChildStdin,
    stdout_reader: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    notif_tx: &mpsc::UnboundedSender<serde_json::Value>,
    state: &mut TurnState,
    input: &[serde_json::Value],
    timeout: Duration,
) -> Result<()> {
    let thread_id = state
        .thread_id
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("turn/start called without thread_id"))?
        .clone();

    let id = send_request(
        stdin,
        "turn/start",
        serde_json::json!({
            "threadId": thread_id,
            "input": input,
        }),
    )
    .await?;

    let resp = tokio::time::timeout(timeout, wait_for_response(id, stdout_reader, notif_tx))
        .await
        .context("turn/start timed out")?
        .context("turn/start failed")?;

    if resp.get("error").is_some() {
        anyhow::bail!(
            "turn/start rejected: {}",
            serde_json::to_string(&resp).unwrap_or_default()
        );
    }

    let turn_id = resp
        .get("result")
        .and_then(|r| r.get("turn"))
        .and_then(|t| t.get("id"))
        .and_then(|i| i.as_str())
        .ok_or_else(|| anyhow::anyhow!("turn/start response missing turn.id"))?
        .to_string();

    state.active_turn_id = Some(turn_id);
    state.fallback_in_flight = false;

    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn handle_prompt(
    a2a_parts: &[serde_json::Value],
    stdin: &mut tokio::process::ChildStdin,
    stdout_reader: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    notif_tx: &mpsc::UnboundedSender<serde_json::Value>,
    state: &mut TurnState,
    event_tx: &mpsc::UnboundedSender<AgentEvent>,
    _session_id: &str,
    stderr_buf: &StderrBuffer,
    child: &mut tokio::process::Child,
    timeout: Duration,
) {
    let (input, temp_files) = match parts_to_codex_input(a2a_parts) {
        Ok(r) => r,
        Err(e) => {
            tracing::error!(error = %e, "failed to prepare prompt input");
            let _ = event_tx.send(AgentEvent::TurnComplete {
                result: TurnResult {
                    agent_session_id: state.thread_id.clone(),
                    error: Some(format!("failed to prepare input: {e}")),
                    error_kind: Some(ErrorKind::ExecutorFailed),
                    output: None,
                    stderr: snapshot_stderr(stderr_buf),
                },
                settled: true,
            });
            return;
        }
    };

    if state.fallback_in_flight {
        state.deferred_prompts.push((input, temp_files));
        let _ = event_tx.send(AgentEvent::Accepted);
        return;
    }

    if state.active_turn_id.is_none() {
        let thread_id = match state.thread_id.as_ref() {
            Some(tid) => tid.clone(),
            None => {
                let _ = child.kill().await;
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: "Prompt received but no thread_id set".to_string(),
                    stderr: snapshot_stderr(stderr_buf),
                    agent_session_id: None,
                });
                cleanup_temp_files(&temp_files);
                return;
            }
        };

        let id_result = send_request(
            stdin,
            "turn/start",
            serde_json::json!({
                "threadId": thread_id,
                "input": input,
            }),
        )
        .await;

        let id = match id_result {
            Ok(id) => id,
            Err(e) => {
                let _ = child.kill().await;
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: format!("failed to send turn/start: {e}"),
                    stderr: snapshot_stderr(stderr_buf),
                    agent_session_id: state.thread_id.clone(),
                });
                cleanup_temp_files(&temp_files);
                return;
            }
        };

        let resp =
            match tokio::time::timeout(timeout, wait_for_response(id, stdout_reader, notif_tx))
                .await
            {
                Ok(Ok(resp)) => resp,
                Ok(Err(e)) => {
                    let _ = child.kill().await;
                    let _ = event_tx.send(AgentEvent::ProcessDied {
                        error: format!("turn/start failed: {e}"),
                        stderr: snapshot_stderr(stderr_buf),
                        agent_session_id: state.thread_id.clone(),
                    });
                    cleanup_temp_files(&temp_files);
                    return;
                }
                Err(_elapsed) => {
                    let _ = child.kill().await;
                    let _ = event_tx.send(AgentEvent::ProcessDied {
                        error: "turn/start timed out".to_string(),
                        stderr: snapshot_stderr(stderr_buf),
                        agent_session_id: state.thread_id.clone(),
                    });
                    cleanup_temp_files(&temp_files);
                    return;
                }
            };

        if resp.get("error").is_some() {
            let _ = child.kill().await;
            let _ = event_tx.send(AgentEvent::ProcessDied {
                error: format!(
                    "turn/start rejected: {}",
                    serde_json::to_string(&resp).unwrap_or_default()
                ),
                stderr: snapshot_stderr(stderr_buf),
                agent_session_id: state.thread_id.clone(),
            });
            cleanup_temp_files(&temp_files);
            return;
        }

        if let Some(turn_id) = resp
            .get("result")
            .and_then(|r| r.get("turn"))
            .and_then(|t| t.get("id"))
            .and_then(|i| i.as_str())
        {
            state.active_turn_id = Some(turn_id.to_string());
        }

        let _ = event_tx.send(AgentEvent::Accepted);
        state.active_turn_temp_files = temp_files;
        state.stop_turn_requested = false;
        return;
    }

    let thread_id = state.thread_id.clone().unwrap_or_default();
    let turn_id = state.active_turn_id.clone().unwrap_or_default();

    let steer_result = send_request(
        stdin,
        "turn/steer",
        serde_json::json!({
            "threadId": thread_id,
            "expectedTurnId": turn_id,
            "input": input,
        }),
    )
    .await;

    let id = match steer_result {
        Ok(id) => id,
        Err(e) => {
            let _ = child.kill().await;
            let _ = event_tx.send(AgentEvent::ProcessDied {
                error: format!("turn/steer transport error: {e}"),
                stderr: snapshot_stderr(stderr_buf),
                agent_session_id: state.thread_id.clone(),
            });
            cleanup_temp_files(&temp_files);
            return;
        }
    };

    let resp =
        match tokio::time::timeout(timeout, wait_for_response(id, stdout_reader, notif_tx)).await {
            Ok(Ok(resp)) => resp,
            Ok(Err(e)) => {
                let _ = child.kill().await;
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: format!("turn/steer failed: {e}"),
                    stderr: snapshot_stderr(stderr_buf),
                    agent_session_id: state.thread_id.clone(),
                });
                cleanup_temp_files(&temp_files);
                return;
            }
            Err(_elapsed) => {
                let _ = child.kill().await;
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: "turn/steer timed out".to_string(),
                    stderr: snapshot_stderr(stderr_buf),
                    agent_session_id: state.thread_id.clone(),
                });
                cleanup_temp_files(&temp_files);
                return;
            }
        };

    if let Some(err) = resp.get("error") {
        let is_deterministic = err
            .get("data")
            .and_then(|d| d.get("activeTurnNotSteerable"))
            .is_some();

        if is_deterministic && !state.stop_turn_requested {
            state.deferred_prompts.push((input, temp_files));
            let _ = event_tx.send(AgentEvent::Accepted);
        } else if state.stop_turn_requested {
            tracing::info!("steer rejected during StopTurn, dropping prompt");
            cleanup_temp_files(&temp_files);
        } else {
            let _ = child.kill().await;
            let _ = event_tx.send(AgentEvent::ProcessDied {
                error: format!(
                    "turn/steer error: {}",
                    serde_json::to_string(err).unwrap_or_default()
                ),
                stderr: snapshot_stderr(stderr_buf),
                agent_session_id: state.thread_id.clone(),
            });
            cleanup_temp_files(&temp_files);
        }
    } else {
        let _ = event_tx.send(AgentEvent::Accepted);
        state.active_turn_temp_files.extend(temp_files);
    }
}

async fn handle_incoming_message(
    msg: &serde_json::Value,
    state: &mut TurnState,
    event_tx: &mpsc::UnboundedSender<AgentEvent>,
    stdin: &mut tokio::process::ChildStdin,
    _session_id: &str,
    stderr_buf: &StderrBuffer,
) {
    let has_id = msg.get("id").is_some();
    let method = msg.get("method").and_then(|m| m.as_str());

    match (has_id, method) {
        (true, Some(method_name)) => {
            let req_id = msg.get("id").expect("checked above");
            handle_server_request(stdin, req_id, method_name, msg).await;
        }

        (true, None) => {
            tracing::debug!(id = ?msg.get("id"), "received response in event loop");
        }

        (false, Some(method_name)) => {
            handle_notification(msg, method_name, state, event_tx, stderr_buf);
        }

        _ => {
            tracing::debug!("ignoring unrecognized JSON-RPC message");
        }
    }
}

/// Classification of server-initiated request methods for testability.
#[derive(Debug, PartialEq)]
enum ServerRequestAction {
    /// Auto-accept approval: respond with `{ decision: "accept" }`
    Accept,
    /// Review-decision approval: respond with `{ decision: "approved" }`
    Approved,
    /// Grant permissions: echo back requested permissions with session scope
    GrantPermissions,
    /// Unsupported in headless mode: respond with JSON-RPC error
    ErrorNotSupported,
    /// Unknown method: respond with JSON-RPC error
    ErrorUnknown,
}

/// Pure classification of server request method → action.
fn classify_server_request(method: &str) -> ServerRequestAction {
    match method {
        "item/permissions/requestApproval" => ServerRequestAction::GrantPermissions,

        "item/commandExecution/requestApproval" | "item/fileChange/requestApproval" => {
            ServerRequestAction::Accept
        }

        "applyPatchApproval" | "execCommandApproval" => ServerRequestAction::Approved,

        "item/tool/requestUserInput"
        | "mcpServer/elicitation/request"
        | "account/chatgptAuthTokens/refresh" => ServerRequestAction::ErrorNotSupported,

        _ => ServerRequestAction::ErrorUnknown,
    }
}

async fn handle_server_request(
    stdin: &mut tokio::process::ChildStdin,
    req_id: &serde_json::Value,
    method: &str,
    msg: &serde_json::Value,
) {
    match classify_server_request(method) {
        ServerRequestAction::Accept => {
            if let Err(e) =
                send_response(stdin, req_id, serde_json::json!({ "decision": "accept" })).await
            {
                tracing::warn!(method = %method, error = %e, "failed to send approval response");
            }
        }
        ServerRequestAction::GrantPermissions => {
            let permissions = msg
                .get("params")
                .and_then(|p| p.get("permissions"))
                .cloned()
                .unwrap_or(serde_json::json!({}));
            if let Err(e) = send_response(
                stdin,
                req_id,
                serde_json::json!({ "permissions": permissions, "scope": "session" }),
            )
            .await
            {
                tracing::warn!(method = %method, error = %e, "failed to send permissions response");
            }
        }
        ServerRequestAction::Approved => {
            if let Err(e) =
                send_response(stdin, req_id, serde_json::json!({ "decision": "approved" })).await
            {
                tracing::warn!(method = %method, error = %e, "failed to send review approval");
            }
        }
        ServerRequestAction::ErrorNotSupported => {
            if let Err(e) =
                send_error_response(stdin, req_id, -32601, "Not supported in headless mode").await
            {
                tracing::warn!(method = %method, error = %e, "failed to send error response");
            }
        }
        ServerRequestAction::ErrorUnknown => {
            tracing::warn!(method = %method, "unknown server request, responding with error");
            if let Err(e) =
                send_error_response(stdin, req_id, -32601, "Method not supported by AgentBeacon")
                    .await
            {
                tracing::warn!(method = %method, error = %e, "failed to send error response");
            }
        }
    }
}

fn handle_notification(
    msg: &serde_json::Value,
    method: &str,
    state: &mut TurnState,
    event_tx: &mpsc::UnboundedSender<AgentEvent>,
    stderr_buf: &StderrBuffer,
) {
    match method {
        "turn/completed" => {
            handle_turn_completed(msg, state, event_tx, stderr_buf);
        }

        "thread/started" => {
            if let Some(tid) = msg
                .get("params")
                .and_then(|p| p.get("thread"))
                .and_then(|t| t.get("id"))
                .and_then(|i| i.as_str())
                && state.thread_id.is_none()
            {
                state.thread_id = Some(tid.to_string());
            }
        }

        "item/agentMessage/delta" => {
            let delta_text = msg
                .get("params")
                .and_then(|p| p.get("delta"))
                .and_then(|d| {
                    d.as_str().map(|s| s.to_string()).or_else(|| {
                        d.get("text")
                            .and_then(|t| t.as_str())
                            .map(|s| s.to_string())
                    })
                })
                .unwrap_or_default();
            if !delta_text.is_empty() {
                let _ = event_tx.send(AgentEvent::Message {
                    output: serde_json::json!({"role": "ROLE_AGENT", "parts": [{"text": delta_text}]}),
                    ephemeral: true,
                });
            }
        }

        "contextCompacted" => {
            let _ = event_tx.send(AgentEvent::Message {
                output: serde_json::json!({"role": "ROLE_AGENT", "parts": [{"data": {"type": "compaction"}}]}),
                ephemeral: false,
            });
        }

        _ => {
            let ephemeral = EPHEMERAL_METHODS.contains(&method);
            let _ = event_tx.send(AgentEvent::Message {
                output: serde_json::json!({"role": "ROLE_AGENT", "parts": [{"data": msg}]}),
                ephemeral,
            });
        }
    }
}

fn is_turn_completed(msg: &serde_json::Value) -> bool {
    msg.get("method").and_then(|m| m.as_str()) == Some("turn/completed")
}

fn handle_turn_completed(
    msg: &serde_json::Value,
    state: &mut TurnState,
    event_tx: &mpsc::UnboundedSender<AgentEvent>,
    stderr_buf: &StderrBuffer,
) {
    let params = msg.get("params").cloned().unwrap_or_default();
    let turn = params.get("turn").cloned().unwrap_or_default();
    let status = turn
        .get("status")
        .and_then(|s| s.as_str())
        .unwrap_or("completed");

    cleanup_temp_files(&state.active_turn_temp_files);
    state.active_turn_temp_files.clear();

    let (error, error_kind) = match status {
        "completed" => (None, None),
        "failed" => {
            let err_msg = turn
                .get("error")
                .and_then(|e| e.get("message"))
                .and_then(|m| m.as_str())
                .unwrap_or("turn failed")
                .to_string();
            (Some(err_msg), Some(ErrorKind::ExecutorFailed))
        }
        "interrupted" => {
            if state.cancel_requested {
                (Some("cancelled".to_string()), Some(ErrorKind::Cancelled))
            } else if state.stop_turn_requested {
                (
                    Some("stopped by user".to_string()),
                    Some(ErrorKind::StoppedByUser),
                )
            } else {
                (Some("interrupted".to_string()), None)
            }
        }
        other => (
            Some(format!("unexpected turn status: {other}")),
            Some(ErrorKind::ExecutorFailed),
        ),
    };

    state.active_turn_id = None;

    let settled = state.settled();

    let result = TurnResult {
        agent_session_id: state.thread_id.clone(),
        error,
        error_kind,
        output: None,
        stderr: snapshot_stderr(stderr_buf),
    };

    let _ = event_tx.send(AgentEvent::TurnComplete { result, settled });

    if !state.deferred_prompts.is_empty() && !state.stop_turn_requested && !state.cancel_requested {
        let deferred = std::mem::take(&mut state.deferred_prompts);
        let mut combined_input = Vec::new();
        let mut combined_temp_files = Vec::new();
        for (input, files) in deferred {
            combined_input.extend(input);
            combined_temp_files.extend(files);
        }

        state.fallback_in_flight = true;
        state.active_turn_temp_files = combined_temp_files;

        state.pending_fallback_input = Some(combined_input);
    }

    if state.stop_turn_requested {
        state.stop_turn_requested = false;
    }
}

async fn shutdown_process(
    child: &mut tokio::process::Child,
    stdin: &mut Option<tokio::process::ChildStdin>,
) {
    stdin.take();

    if let Some(pid) = child.id() {
        let _ = nix::sys::signal::kill(
            nix::unistd::Pid::from_raw(pid as i32),
            nix::sys::signal::Signal::SIGINT,
        );
    }

    match tokio::time::timeout(SHUTDOWN_SIGINT_TIMEOUT, child.wait()).await {
        Ok(_) => {}
        Err(_) => {
            tracing::warn!("Codex process did not exit after SIGINT, sending SIGKILL");
            let _ = child.kill().await;
        }
    }
}

fn cleanup_deferred_temp_files(state: &mut TurnState) {
    for (_input, files) in &state.deferred_prompts {
        cleanup_temp_files(files);
    }
}

fn cleanup_state_temp_files(state: &mut TurnState) {
    cleanup_temp_files(&state.active_turn_temp_files);
    cleanup_deferred_temp_files(state);
}

/// Minimum supported Codex app-server version.
const MIN_CODEX_VERSION: (u64, u64, u64) = (0, 118, 0);

/// Parse a semver-like version string (e.g. "0.118.3") into (major, minor, patch).
/// Returns None for unparseable strings.
fn parse_version(version: &str) -> Option<(u64, u64, u64)> {
    let parts: Vec<&str> = version.split('.').collect();
    if parts.len() < 3 {
        return None;
    }
    Some((
        parts[0].parse().ok()?,
        parts[1].parse().ok()?,
        parts[2].parse().ok()?,
    ))
}

/// Check whether the server version meets the minimum requirement.
/// Returns Ok(()) if the version is sufficient or absent (graceful degradation).
/// Returns Err with a message if the version is below the minimum.
fn check_server_version(server_info: &serde_json::Value) -> Result<()> {
    let version_str = server_info.get("version").and_then(|v| v.as_str());

    match version_str {
        None => {
            tracing::warn!(
                "Codex version field absent in initialize response — proceeding without version gate"
            );
            Ok(())
        }
        Some(v) => match parse_version(v) {
            None => {
                tracing::warn!(version = %v, "unparseable Codex version, proceeding");
                Ok(())
            }
            Some(parsed) => {
                if parsed < MIN_CODEX_VERSION {
                    anyhow::bail!(
                        "Codex version {v} is below minimum required {}.{}.{}",
                        MIN_CODEX_VERSION.0,
                        MIN_CODEX_VERSION.1,
                        MIN_CODEX_VERSION.2
                    );
                }
                Ok(())
            }
        },
    }
}
