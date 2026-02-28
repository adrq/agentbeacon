//! ACP executor adapter wrapping existing ACP subprocess management.
//!
//! Background task pattern: `start()` initializes the ACP subprocess (JSON-RPC
//! initialize + session/new) then spawns a `tokio::select!`-based event loop
//! that concurrently polls ACP protocol messages and worker commands. This
//! ensures Cancel can be received even during an active prompt turn.

use anyhow::{Context, Result};
use common::Message;
use serde::Deserialize;
use std::collections::{HashMap, VecDeque};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::sync::mpsc;
use tokio::time::Instant;

use super::{
    AgentCommand, AgentEvent, ErrorKind, ExecutorHandle, SessionConfig, StderrBuffer, TurnResult,
    new_stderr_buffer, push_stderr_line, snapshot_stderr,
};
use crate::acp::executor::{
    JsonRpcClient, JsonRpcMessage, LegacyAcpConfig, handle_permission_request,
    handle_permission_request_cancelled, handle_session_update, read_jsonrpc_lines,
    send_initialize, send_session_new, spawn_acp_subprocess, terminate_subprocess,
    translate_a2a_parts_to_acp_content,
};
use crate::acp::protocol::{JsonRpcResponse, SessionPromptParams, SessionPromptResult};

const CANCEL_GRACE_PERIOD: Duration = Duration::from_secs(3);

/// ACP agent configuration parsed from task_payload.agent_config
#[derive(Debug, Deserialize, Clone)]
pub struct AcpConfig {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub timeout: Option<u64>,
    pub env: Option<HashMap<String, String>>,
}

impl AcpConfig {
    pub fn validate(&self) -> Result<()> {
        if self.command.is_empty() {
            anyhow::bail!("ACP agent command cannot be empty");
        }
        if let Some(timeout) = self.timeout
            && timeout == 0
        {
            anyhow::bail!("ACP agent timeout must be greater than 0");
        }
        Ok(())
    }

    fn to_legacy(&self) -> LegacyAcpConfig {
        LegacyAcpConfig {
            command: self.command.clone(),
            args: self.args.clone(),
            timeout: self.timeout,
            env: self.env.clone(),
        }
    }
}

/// Start the ACP executor: spawn the subprocess, initialize JSON-RPC,
/// create session, then return an ExecutorHandle.
pub async fn start(config: SessionConfig) -> Result<ExecutorHandle> {
    let acp_config: AcpConfig = serde_json::from_value(config.agent_config.clone())
        .context("failed to parse ACP agent config")?;
    acp_config.validate()?;

    let legacy_config = acp_config.to_legacy();
    let init_timeout = Duration::from_secs(acp_config.timeout.unwrap_or(30));

    let mut child = spawn_acp_subprocess(&legacy_config)?;

    let stdin = child
        .stdin
        .take()
        .context("failed to get subprocess stdin")?;
    let stdout = child
        .stdout
        .take()
        .context("failed to get subprocess stdout")?;
    let stderr = child
        .stderr
        .take()
        .context("failed to get subprocess stderr")?;

    // Stderr drain: capture to buffer + forward to tracing
    let stderr_buf = new_stderr_buffer();
    let buf_clone = stderr_buf.clone();
    tokio::spawn(async move {
        let reader = BufReader::new(stderr);
        let mut lines = reader.lines();
        while let Ok(Some(line)) = lines.next_line().await {
            tracing::debug!(target: "acp_executor", "{}", line);
            push_stderr_line(&buf_clone, line);
        }
    });

    let (notification_tx, mut notification_rx) = mpsc::unbounded_channel();

    let reader_handle = tokio::spawn(read_jsonrpc_lines(stdout, notification_tx));

    let mut client = JsonRpcClient::new(stdin);

    // Initialize
    let init_result = tokio::time::timeout(
        init_timeout,
        send_initialize(&mut client, &mut notification_rx),
    )
    .await
    .context("initialize timed out")??;

    if init_result.protocol_version != 1 {
        let _ = child.kill().await;
        return Err(anyhow::anyhow!(
            "unsupported protocol version: {} (expected 1)",
            init_result.protocol_version
        ));
    }

    // Session/new
    let acp_session_id = tokio::time::timeout(
        init_timeout,
        send_session_new(
            &mut client,
            &mut notification_rx,
            &config.cwd,
            &config.scheduler_url,
            &config.session_id,
        ),
    )
    .await
    .context("session/new timed out")??;

    tracing::info!(
        execution_id = %config.execution_id,
        acp_session_id = %acp_session_id,
        "ACP agent started"
    );

    // Channels for the worker main loop
    let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();
    let (event_tx, event_rx) = mpsc::unbounded_channel();

    // Emit Init event immediately since ACP init happens synchronously above
    let _ = event_tx.send(AgentEvent::Init {
        session_id: acp_session_id.clone(),
    });

    // Background task: select!-based event loop
    let task_handle = tokio::spawn(background_task(
        child,
        client,
        notification_rx,
        reader_handle,
        cmd_rx,
        event_tx,
        acp_session_id,
        stderr_buf,
        config.inactivity_timeout,
    ));

    Ok(ExecutorHandle {
        cmd_tx,
        event_rx,
        task_handle,
    })
}

// ---------------------------------------------------------------------------
// PromptPhase state machine
// ---------------------------------------------------------------------------

/// Tracks the current state of the ACP prompt turn.
enum PromptPhase {
    Idle,
    AwaitingResponse {
        request_id: String,
        update_history: Vec<Message>,
    },
    Cancelling {
        request_id: String,
        update_history: Vec<Message>,
        deadline: Instant,
    },
}

impl PromptPhase {
    fn is_idle(&self) -> bool {
        matches!(self, PromptPhase::Idle)
    }

    fn is_cancelling(&self) -> bool {
        matches!(self, PromptPhase::Cancelling { .. })
    }

    fn is_active(&self) -> bool {
        !self.is_idle()
    }

    fn has_request_id(&self, id: &str) -> bool {
        match self {
            PromptPhase::AwaitingResponse { request_id, .. }
            | PromptPhase::Cancelling { request_id, .. } => request_id == id,
            PromptPhase::Idle => false,
        }
    }

    fn update_history_mut(&mut self) -> Option<&mut Vec<Message>> {
        match self {
            PromptPhase::AwaitingResponse { update_history, .. }
            | PromptPhase::Cancelling { update_history, .. } => Some(update_history),
            PromptPhase::Idle => None,
        }
    }

    fn deadline(&self) -> Instant {
        match self {
            PromptPhase::Cancelling { deadline, .. } => *deadline,
            // Far future: the `if phase.is_cancelling()` guard prevents this branch
            // from firing, but tokio::select! may still create the Sleep future.
            _ => Instant::now() + Duration::from_secs(86400),
        }
    }

    /// Transition from AwaitingResponse → Cancelling.
    fn begin_cancel(self) -> Self {
        match self {
            PromptPhase::AwaitingResponse {
                request_id,
                update_history,
            } => PromptPhase::Cancelling {
                request_id,
                update_history,
                deadline: Instant::now() + CANCEL_GRACE_PERIOD,
            },
            other => other,
        }
    }

    /// Consume phase and return update_history (transitions to Idle implicitly).
    fn take_history(self) -> Vec<Message> {
        match self {
            PromptPhase::AwaitingResponse { update_history, .. }
            | PromptPhase::Cancelling { update_history, .. } => update_history,
            PromptPhase::Idle => Vec::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// Background task
// ---------------------------------------------------------------------------

/// Event loop that concurrently polls ACP protocol messages and worker
/// commands via `tokio::select!`. This ensures Cancel is received even
/// during an active prompt turn (the root cause of the original bug).
#[allow(clippy::too_many_arguments)]
async fn background_task(
    mut child: tokio::process::Child,
    mut client: JsonRpcClient,
    mut notification_rx: mpsc::UnboundedReceiver<JsonRpcMessage>,
    reader_handle: tokio::task::JoinHandle<()>,
    mut cmd_rx: mpsc::UnboundedReceiver<AgentCommand>,
    event_tx: mpsc::UnboundedSender<AgentEvent>,
    session_id: String,
    stderr_buf: StderrBuffer,
    inactivity_timeout: std::time::Duration,
) {
    let mut phase = PromptPhase::Idle;
    let mut pending_prompts: VecDeque<String> = VecDeque::new();
    let mut last_activity = Instant::now();

    loop {
        tokio::select! {
            // Branch 1: ACP protocol messages — always polled regardless of phase.
            // In Idle: discards stale updates, detects subprocess death.
            // In AwaitingResponse/Cancelling: processes prompt responses + updates.
            msg = notification_rx.recv() => {
                match msg {
                    Some(JsonRpcMessage::Response(resp)) if phase.has_request_id(&resp.id) => {
                        last_activity = Instant::now();
                        let was_cancelling = phase.is_cancelling();
                        let history = std::mem::replace(&mut phase, PromptPhase::Idle)
                            .take_history();
                        let turn = build_turn_result(&resp, history, &session_id, &stderr_buf);
                        let _ = event_tx.send(AgentEvent::TurnComplete(turn));

                        // Drain pending prompt queue (skip if cancelling — session ending)
                        if !was_cancelling {
                            if let Some(text) = pending_prompts.pop_front() {
                                let parts = vec![serde_json::json!({"type": "text", "text": text})];
                                match send_prompt(&mut client, &session_id, parts).await {
                                    Ok(request_id) => {
                                        last_activity = Instant::now();
                                        phase = PromptPhase::AwaitingResponse {
                                            request_id,
                                            update_history: Vec::new(),
                                        };
                                    }
                                    Err(e) => {
                                        let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                            agent_session_id: Some(session_id.clone()),
                                            error: Some(format!("failed to send queued prompt: {e}")),
                                            error_kind: Some(ErrorKind::ExecutorFailed),
                                            output: None,
                                            stderr: snapshot_stderr(&stderr_buf),
                                        }));
                                    }
                                }
                            }
                        } else {
                            // Discard queued prompts on cancel
                            pending_prompts.clear();
                        }
                    }
                    Some(JsonRpcMessage::Response(_)) => {
                        last_activity = Instant::now();
                    }
                    Some(JsonRpcMessage::Notification(notif)) if notif.method == "session/update" => {
                        last_activity = Instant::now();
                        if let Some(history) = phase.update_history_mut() {
                            match handle_session_update(&notif.params, history) {
                                Ok(()) => {
                                    if let Some(last_msg) = history.last() {
                                        let parts: Vec<_> = last_msg.parts.iter()
                                            .filter_map(|p| serde_json::to_value(p).ok())
                                            .collect();
                                        let _ = event_tx.send(AgentEvent::Message {
                                            output: serde_json::json!({"role": &last_msg.role, "parts": parts}),
                                        });
                                    }
                                }
                                Err(e) => {
                                    tracing::warn!(error = %e, "failed to process session/update");
                                }
                            }
                        }
                    }
                    Some(JsonRpcMessage::Notification(_)) => {
                        last_activity = Instant::now();
                    }
                    Some(JsonRpcMessage::Request(req)) => {
                        last_activity = Instant::now();
                        if req.method == "session/request_permission" {
                            let result = if phase.is_cancelling() {
                                handle_permission_request_cancelled(&mut client, &req.id).await
                            } else {
                                handle_permission_request(&mut client, &req.id, &req.params).await
                            };
                            if let Err(e) = result {
                                tracing::warn!(error = %e, "failed to handle permission request");
                            }
                        } else {
                            tracing::warn!(method = %req.method, "Unsupported agent→worker request");
                            if let Err(e) = client.send_error_response(
                                req.id,
                                -32601,
                                "Method not found".to_string(),
                            ).await {
                                tracing::warn!(error = %e, "failed to send error response");
                            }
                        }
                    }
                    Some(JsonRpcMessage::ParseError(line)) => {
                        last_activity = Instant::now();
                        if phase.is_active() {
                            let truncated: String = line.chars().take(80).collect();
                            tracing::error!(
                                line_preview = %truncated,
                                "Malformed JSON-RPC response during active prompt"
                            );
                            phase = PromptPhase::Idle;
                            pending_prompts.clear();
                            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                agent_session_id: Some(session_id.clone()),
                                error: Some("malformed JSON-RPC response".into()),
                                error_kind: Some(ErrorKind::ExecutorFailed),
                                output: None,
                                stderr: snapshot_stderr(&stderr_buf),
                            }));
                        }
                    }
                    None => {
                        // Subprocess died — detected regardless of phase
                        let exit_status = child.try_wait();
                        let exit_info = match exit_status {
                            Ok(Some(status)) => format!("exit code: {status}"),
                            Ok(None) => "still running".to_string(),
                            Err(e) => format!("error checking status: {e}"),
                        };
                        let _ = event_tx.send(AgentEvent::ProcessDied {
                            error: format!("ACP subprocess died ({exit_info})"),
                            stderr: snapshot_stderr(&stderr_buf),
                        });
                        reader_handle.abort();
                        terminate_subprocess(&mut child).await;
                        return;
                    }
                }
            }

            // Branch 2: Worker commands
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(AgentCommand::Start(task_payload)) => {
                        if !phase.is_idle() {
                            tracing::error!("Start received while not idle");
                            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                agent_session_id: Some(session_id.clone()),
                                error: Some("Start received while prompt in progress".into()),
                                error_kind: Some(ErrorKind::ExecutorFailed),
                                output: None,
                                stderr: None,
                            }));
                            continue;
                        }
                        let prompt_parts = match extract_acp_content(
                            &task_payload, &session_id, &event_tx,
                        ) {
                            Some(p) => p,
                            None => continue, // error already emitted
                        };
                        match send_prompt(&mut client, &session_id, prompt_parts).await {
                            Ok(request_id) => {
                                last_activity = Instant::now();
                                phase = PromptPhase::AwaitingResponse {
                                    request_id,
                                    update_history: Vec::new(),
                                };
                            }
                            Err(e) => {
                                let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                    agent_session_id: Some(session_id.clone()),
                                    error: Some(format!("failed to send prompt: {e}")),
                                    error_kind: Some(ErrorKind::ExecutorFailed),
                                    output: None,
                                    stderr: snapshot_stderr(&stderr_buf),
                                }));
                            }
                        }
                    }
                    Some(AgentCommand::Prompt(text)) => {
                        if !phase.is_idle() {
                            tracing::debug!("Prompt received while busy, queuing");
                            pending_prompts.push_back(text);
                            continue;
                        }
                        let prompt_parts = vec![serde_json::json!({"type": "text", "text": text})];
                        match send_prompt(&mut client, &session_id, prompt_parts).await {
                            Ok(request_id) => {
                                last_activity = Instant::now();
                                phase = PromptPhase::AwaitingResponse {
                                    request_id,
                                    update_history: Vec::new(),
                                };
                            }
                            Err(e) => {
                                let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                                    agent_session_id: Some(session_id.clone()),
                                    error: Some(format!("failed to send prompt: {e}")),
                                    error_kind: Some(ErrorKind::ExecutorFailed),
                                    output: None,
                                    stderr: snapshot_stderr(&stderr_buf),
                                }));
                            }
                        }
                    }
                    Some(AgentCommand::Cancel) => {
                        if let PromptPhase::AwaitingResponse { .. } = &phase {
                            tracing::info!(session_id = %session_id, "Sending session/cancel");
                            let cancel_params = serde_json::json!({
                                "sessionId": session_id
                            });
                            if let Err(e) = client
                                .send_notification("session/cancel", cancel_params)
                                .await
                            {
                                tracing::warn!(error = %e, "failed to send session/cancel");
                            }
                            phase = phase.begin_cancel();
                        }
                        // Idle or already Cancelling → no-op
                    }
                    Some(AgentCommand::Stop) | None => {
                        terminate_subprocess(&mut child).await;
                        reader_handle.abort();
                        return;
                    }
                }
            }

            // Branch 3: Cancel grace period timeout
            _ = tokio::time::sleep_until(phase.deadline()), if phase.is_cancelling() => {
                tracing::warn!("Agent did not respond to session/cancel within grace period");
                phase = PromptPhase::Idle;
                pending_prompts.clear();
                let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                    agent_session_id: Some(session_id.clone()),
                    error: Some("Agent did not acknowledge cancellation".into()),
                    error_kind: Some(ErrorKind::Cancelled),
                    output: None,
                    stderr: snapshot_stderr(&stderr_buf),
                }));
            }

            // Branch 4: Inactivity timeout (only during active, non-cancelling turns)
            _ = tokio::time::sleep_until(last_activity + inactivity_timeout),
                if phase.is_active() && !phase.is_cancelling() => {
                tracing::warn!(
                    session_id = %session_id,
                    "ACP executor stalled: no output for {}s",
                    inactivity_timeout.as_secs()
                );
                let _ = event_tx.send(AgentEvent::ProcessDied {
                    error: format!("executor stalled: no output for {}s", inactivity_timeout.as_secs()),
                    stderr: snapshot_stderr(&stderr_buf),
                });
                terminate_subprocess(&mut child).await;
                reader_handle.abort();
                return;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Extract ACP content parts from a task payload.
/// All payloads use A2A format: `{message: {role, parts}}`.
fn extract_acp_content(
    task_payload: &serde_json::Value,
    session_id: &str,
    event_tx: &mpsc::UnboundedSender<AgentEvent>,
) -> Option<Vec<serde_json::Value>> {
    let message = match task_payload.get("message") {
        Some(m) => m,
        None => {
            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                agent_session_id: Some(session_id.to_string()),
                error: Some("task_payload missing message field".into()),
                error_kind: Some(ErrorKind::ExecutorFailed),
                output: None,
                stderr: None,
            }));
            return None;
        }
    };
    let parts = match message.get("parts").and_then(|p| p.as_array()) {
        Some(p) => p,
        None => {
            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                agent_session_id: Some(session_id.to_string()),
                error: Some("message missing parts array".into()),
                error_kind: Some(ErrorKind::ExecutorFailed),
                output: None,
                stderr: None,
            }));
            return None;
        }
    };
    match translate_a2a_parts_to_acp_content(parts) {
        Ok(translated) => Some(translated),
        Err(e) => {
            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                agent_session_id: Some(session_id.to_string()),
                error: Some(format!("failed to translate parts: {e}")),
                error_kind: Some(ErrorKind::ExecutorFailed),
                output: None,
                stderr: None,
            }));
            None
        }
    }
}

/// Send a session/prompt JSON-RPC request, returning the request_id.
async fn send_prompt(
    client: &mut JsonRpcClient,
    session_id: &str,
    prompt_parts: Vec<serde_json::Value>,
) -> Result<String> {
    let prompt_params = SessionPromptParams {
        session_id: session_id.to_string(),
        prompt: prompt_parts,
    };
    client
        .send_request("session/prompt", serde_json::to_value(&prompt_params)?)
        .await
}

/// Build a TurnResult from an ACP session/prompt response.
fn build_turn_result(
    resp: &JsonRpcResponse,
    update_history: Vec<Message>,
    session_id: &str,
    stderr_buf: &StderrBuffer,
) -> TurnResult {
    // JSON-RPC level error
    if let Some(error) = &resp.error {
        return TurnResult {
            agent_session_id: Some(session_id.to_string()),
            error: Some(format!("{} (code: {})", error.message, error.code)),
            error_kind: Some(ErrorKind::ExecutorFailed),
            output: None,
            stderr: snapshot_stderr(stderr_buf),
        };
    }

    // Parse SessionPromptResult from response
    let prompt_result: SessionPromptResult = match resp
        .result
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("session/prompt response missing result"))
        .and_then(|v| serde_json::from_value(v.clone()).map_err(Into::into))
    {
        Ok(r) => r,
        Err(e) => {
            return TurnResult {
                agent_session_id: Some(session_id.to_string()),
                error: Some(format!("{e:#}")),
                error_kind: Some(ErrorKind::ExecutorFailed),
                output: None,
                stderr: snapshot_stderr(stderr_buf),
            };
        }
    };

    let is_error = matches!(
        prompt_result.stop_reason.as_str(),
        "error" | "cancelled" | "refusal"
    );

    let (error, error_kind) = if !is_error {
        (None, None)
    } else {
        let error_kind = match prompt_result.stop_reason.as_str() {
            "cancelled" => Some(ErrorKind::Cancelled),
            _ => Some(ErrorKind::ExecutorFailed),
        };
        let error = Some(
            prompt_result
                .error
                .unwrap_or_else(|| prompt_result.stop_reason.clone()),
        );
        (error, error_kind)
    };

    // Consolidate agent-role messages into output
    let agent_parts: Vec<serde_json::Value> = update_history
        .iter()
        .filter(|m| m.role == "agent")
        .flat_map(|m| m.parts.iter().filter_map(|p| serde_json::to_value(p).ok()))
        .collect();

    let output = if agent_parts.is_empty() {
        None
    } else {
        Some(serde_json::json!({"role": "agent", "parts": agent_parts}))
    };

    let stderr = if error.is_some() {
        snapshot_stderr(stderr_buf)
    } else {
        None
    };

    TurnResult {
        agent_session_id: Some(session_id.to_string()),
        error,
        error_kind,
        output,
        stderr,
    }
}
