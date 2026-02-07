//! ACP protocol executor for worker ↔ agent communication over stdio.
//!
//! Implements the ACP (Agent Client Protocol) lifecycle:
//! 1. Spawn subprocess with stdio communication
//! 2. Execute initialize → session/new → session/prompt protocol sequence
//! 3. Handle session/update notifications and translate to A2A Message objects
//! 4. Support graceful cancellation with session/cancel
//! 5. Map SessionPromptResult to A2ATaskStatus

use crate::agent::A2ATaskMetadata;
use crate::config::AcpConfig;
use crate::sync::{TaskAssignment, TaskResult};
use anyhow::{Context, Result};
use common::{A2ATaskStatus, Message, Part};
use serde_json::Value;
use std::panic::catch_unwind;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{Mutex, mpsc};
use tokio::time::timeout;
use uuid::Uuid;

use super::protocol::*;

const DEFAULT_TIMEOUT_SECS: u64 = 30;
const TERMINATION_TIMEOUT_SECS: u64 = 2;
const CANCEL_GRACE_PERIOD_SECS: u64 = 10;

/// Execute ACP task with comprehensive error handling and logging
pub async fn execute_acp_task(
    acp_config: &AcpConfig,
    task: &TaskAssignment,
    metadata: Arc<Mutex<A2ATaskMetadata>>,
) -> TaskResult {
    tracing::info!(
        execution_id = %task.execution_id,
        node_id = %task.node_id,
        agent = %task.agent,
        "ACP task started"
    );

    let result = match execute_acp_task_inner(acp_config, task, metadata).await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!(
                event = "task_failed",
                execution_id = %task.execution_id,
                error = %e,
                "ACP task failed"
            );
            TaskResult {
                execution_id: task.execution_id.clone(),
                node_id: task.node_id.clone(),
                task_status: A2ATaskStatus::failed(format!("{e:#}")),
                artifacts: None,
            }
        }
    };

    tracing::info!(
        execution_id = %task.execution_id,
        state = %result.task_status.state,
        "ACP task completed"
    );

    result
}

/// Core ACP protocol execution flow
async fn execute_acp_task_inner(
    acp_config: &AcpConfig,
    task: &TaskAssignment,
    metadata: Arc<Mutex<A2ATaskMetadata>>,
) -> Result<TaskResult> {
    tracing::debug!(
        execution_id = %task.execution_id,
        node_id = %task.node_id,
        "Starting ACP task execution"
    );

    let cwd = extract_and_validate_cwd(task)?;
    tracing::debug!(cwd = %cwd, "Extracted cwd from task metadata");

    let mut child = spawn_acp_subprocess(acp_config)?;
    tracing::debug!("Spawned ACP subprocess");

    let stdin = child
        .stdin
        .take()
        .context("failed to get subprocess stdin")?;
    let stdout = child
        .stdout
        .take()
        .context("failed to get subprocess stdout")?;

    let (notification_tx, mut notification_rx) = mpsc::unbounded_channel();
    let (cancel_tx, mut cancel_rx) = mpsc::unbounded_channel();

    // Spawn background task to read responses and notifications
    let reader_handle = tokio::spawn(read_jsonrpc_lines(stdout, notification_tx));

    // Create JSON-RPC client
    let mut client = JsonRpcClient::new(stdin);

    // Determine timeout for initialize/session/new
    let init_timeout = Duration::from_secs(acp_config.timeout.unwrap_or(DEFAULT_TIMEOUT_SECS));

    // Execute ACP protocol sequence
    // 1. Initialize
    tracing::info!(
        event = "initialize_sent",
        agent = %acp_config.command,
        timeout_secs = init_timeout.as_secs(),
        "Sending ACP initialize request"
    );
    let init_result = timeout(
        init_timeout,
        send_initialize(&mut client, &mut notification_rx),
    )
    .await
    .context("initialize timed out")??;
    tracing::info!(
        event = "initialize_completed",
        protocol_version = init_result.protocol_version,
        "ACP initialize completed successfully"
    );

    // Verify protocol version
    if init_result.protocol_version != 1 {
        tracing::error!(
            event = "protocol_version_error",
            protocol_version = init_result.protocol_version,
            expected = 1,
            "Unsupported ACP protocol version"
        );
        // Kill subprocess before returning error
        let _ = child.kill().await;
        return Err(anyhow::anyhow!(
            "unsupported protocol version: {} (expected 1)",
            init_result.protocol_version
        ));
    }

    // 2. Session/new
    tracing::info!(
        event = "session_new_sent",
        cwd = %cwd,
        "Sending ACP session/new request"
    );
    let session_id = timeout(
        init_timeout,
        send_session_new(&mut client, &mut notification_rx, &cwd),
    )
    .await
    .context("session/new timed out")??;
    tracing::info!(
        event = "session_created",
        session_id = %session_id,
        cwd = %cwd,
        "ACP session created successfully"
    );

    // Populate ACP metadata for cancellation support
    {
        tracing::debug!("Populating ACP metadata");
        let mut meta = metadata.lock().await;
        meta.acp_session_id = Some(session_id.clone());
        meta.acp_cancel_tx = Some(cancel_tx.clone());
        tracing::debug!("ACP metadata populated");
    }

    // 3. Session/prompt (no timeout, but monitors cancellation channel)
    tracing::info!(
        event = "prompt_sent",
        session_id = %session_id,
        "Sending ACP session/prompt request"
    );
    let mut update_history: Vec<Message> = Vec::new();
    let prompt_result = send_session_prompt(
        &mut client,
        &mut notification_rx,
        &mut cancel_rx,
        &session_id,
        &task.task,
        &mut update_history,
    )
    .await?;
    tracing::info!(
        event = "prompt_completed",
        session_id = %session_id,
        stop_reason = %prompt_result.stop_reason,
        update_count = update_history.len(),
        "ACP session/prompt completed"
    );

    // Close stdin to signal subprocess that no more input is coming
    tracing::debug!("Dropping client to close stdin");
    drop(client);
    tracing::debug!("Client dropped, stdin closed");

    // Terminate subprocess within 2 seconds
    tracing::debug!("Terminating subprocess");
    terminate_subprocess(&mut child).await;
    tracing::debug!("Subprocess terminated");

    // Wait for reader task to complete
    tracing::debug!("Waiting for reader task");
    let _ = reader_handle.await;
    tracing::debug!("Reader task completed");

    // Translate ACP result to A2A TaskResult
    tracing::debug!("Translating to TaskResult");
    let task_result = translate_to_task_result(task, prompt_result, update_history);
    tracing::debug!(state = %task_result.task_status.state, "Task result created");

    tracing::debug!("Returning task result");
    Ok(task_result)
}

/// Extract cwd from task metadata and validate it's an absolute path
fn extract_and_validate_cwd(task: &TaskAssignment) -> Result<String> {
    let metadata = task
        .task
        .get("metadata")
        .ok_or_else(|| anyhow::anyhow!("Missing task.metadata"))?;

    let cwd = metadata
        .get("cwd")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("Missing cwd in task metadata"))?;

    if cwd.is_empty() {
        return Err(anyhow::anyhow!("Missing cwd in task metadata"));
    }

    if !cwd.starts_with('/') {
        return Err(anyhow::anyhow!(
            "cwd must be absolute path (must start with /), got: {cwd}"
        ));
    }

    Ok(cwd.to_string())
}

/// Spawn ACP subprocess with stdio communication
fn spawn_acp_subprocess(acp_config: &AcpConfig) -> Result<Child> {
    // Panic protection for Command::env (Windows platform issue)
    let mut cmd = Command::new(&acp_config.command);
    cmd.args(&acp_config.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit()) // Inherit stderr for debugging
        .kill_on_drop(true);

    // Apply env vars with panic protection
    if let Some(env_vars) = &acp_config.env {
        let result = catch_unwind(std::panic::AssertUnwindSafe(|| {
            for (key, value) in env_vars {
                cmd.env(key, value);
            }
        }));

        if result.is_err() {
            return Err(anyhow::anyhow!(
                "failed to set environment variables (platform limitation)"
            ));
        }
    }

    cmd.spawn().context("failed to spawn ACP subprocess")
}

/// Terminate subprocess within 2 seconds using SIGTERM
async fn terminate_subprocess(child: &mut Child) {
    let term_timeout = Duration::from_secs(TERMINATION_TIMEOUT_SECS);

    match timeout(term_timeout, child.wait()).await {
        Ok(Ok(_)) => {
            tracing::debug!("Subprocess exited gracefully");
        }
        Ok(Err(e)) => {
            tracing::warn!(
                event = "subprocess_exit_error",
                error = %e,
                "Error waiting for subprocess to exit"
            );
        }
        Err(_) => {
            tracing::warn!(
                event = "subprocess_timeout",
                timeout_secs = term_timeout.as_secs(),
                "Subprocess did not exit within timeout, sending SIGTERM"
            );
            let _ = child.kill().await;
        }
    }
}

/// Send initialize request and wait for response
async fn send_initialize(
    client: &mut JsonRpcClient,
    notification_rx: &mut mpsc::UnboundedReceiver<JsonRpcMessage>,
) -> Result<InitializeResult> {
    let init_params = InitializeParams {
        protocol_version: 1,
        client_capabilities: ClientCapabilities {
            fs: None,
            terminal: None,
        },
    };

    let request_id = client
        .send_request("initialize", serde_json::to_value(&init_params)?)
        .await?;

    // Wait for response
    while let Some(msg) = notification_rx.recv().await {
        match msg {
            JsonRpcMessage::Response(resp) if resp.id == request_id => {
                if let Some(error) = resp.error {
                    tracing::error!(
                        event = "initialize_error",
                        code = error.code,
                        message = %error.message,
                        "ACP initialize returned error"
                    );
                    return Err(anyhow::anyhow!(
                        "initialize error: {} (code: {})",
                        error.message,
                        error.code
                    ));
                }

                let result: InitializeResult = serde_json::from_value(
                    resp.result
                        .ok_or_else(|| anyhow::anyhow!("initialize response missing result"))?,
                )?;
                return Ok(result);
            }
            JsonRpcMessage::Response(_) => {
                // Different request ID, ignore
            }
            JsonRpcMessage::Notification(_) => {
                // Ignore notifications during initialize
            }
            JsonRpcMessage::Request(req) => {
                // Ignore agent requests during initialize (unexpected)
                tracing::warn!(
                    event = "unexpected_request",
                    method = %req.method,
                    phase = "initialize",
                    "Unexpected agent request during initialize"
                );
            }
            JsonRpcMessage::ParseError(line) => {
                // Malformed JSON-RPC response - fail task immediately
                tracing::error!(
                    event = "parse_error",
                    phase = "initialize",
                    line = %line,
                    "Malformed JSON-RPC response during initialize"
                );
                return Err(anyhow::anyhow!(
                    "malformed JSON-RPC response during initialize: {line}"
                ));
            }
        }
    }

    tracing::error!(
        event = "subprocess_closed",
        phase = "initialize",
        "Subprocess closed before initialize response"
    );
    Err(anyhow::anyhow!(
        "subprocess closed before initialize response"
    ))
}

/// Send session/new request and wait for response
async fn send_session_new(
    client: &mut JsonRpcClient,
    notification_rx: &mut mpsc::UnboundedReceiver<JsonRpcMessage>,
    cwd: &str,
) -> Result<String> {
    let session_params = SessionNewParams {
        cwd: cwd.to_string(),
        mcp_servers: vec![], // Empty array
    };

    let request_id = client
        .send_request("session/new", serde_json::to_value(&session_params)?)
        .await?;

    // Wait for response
    while let Some(msg) = notification_rx.recv().await {
        match msg {
            JsonRpcMessage::Response(resp) if resp.id == request_id => {
                if let Some(error) = resp.error {
                    tracing::error!(
                        event = "session_new_error",
                        code = error.code,
                        message = %error.message,
                        "ACP session/new returned error"
                    );
                    return Err(anyhow::anyhow!(
                        "session/new error: {} (code: {})",
                        error.message,
                        error.code
                    ));
                }

                let result: SessionNewResult = serde_json::from_value(
                    resp.result
                        .ok_or_else(|| anyhow::anyhow!("session/new response missing result"))?,
                )?;
                return Ok(result.session_id);
            }
            JsonRpcMessage::Response(_) => {
                // Different request ID, ignore
            }
            JsonRpcMessage::Notification(_) => {
                // Ignore notifications during session/new
            }
            JsonRpcMessage::Request(req) => {
                // Ignore agent requests during session/new (unexpected)
                tracing::warn!(
                    event = "unexpected_request",
                    method = %req.method,
                    phase = "session_new",
                    "Unexpected agent request during session/new"
                );
            }
            JsonRpcMessage::ParseError(line) => {
                // Malformed JSON-RPC response - fail task immediately
                tracing::error!(
                    event = "parse_error",
                    phase = "session_new",
                    line = %line,
                    "Malformed JSON-RPC response during session/new"
                );
                return Err(anyhow::anyhow!(
                    "malformed JSON-RPC response during session/new: {line}"
                ));
            }
        }
    }

    tracing::error!(
        event = "subprocess_closed",
        phase = "session_new",
        "Subprocess closed before session/new response"
    );
    Err(anyhow::anyhow!(
        "subprocess closed before session/new response"
    ))
}

/// Translate A2A Message parts to ACP ContentBlock array
fn translate_a2a_parts_to_acp_content(parts: &[Value]) -> Result<Vec<Value>> {
    parts
        .iter()
        .map(|part| {
            let kind = part
                .get("kind")
                .and_then(|k| k.as_str())
                .ok_or_else(|| anyhow::anyhow!("A2A part missing kind field"))?;

            match kind {
                "text" => {
                    let text = part.get("text").and_then(|t| t.as_str()).unwrap_or("");
                    Ok(serde_json::json!({
                        "type": "text",
                        "text": text
                    }))
                }
                "data" => {
                    // A2A data parts don't map cleanly to ACP ContentBlock
                    // Skip data parts as they're typically metadata, not user-facing content
                    tracing::warn!(
                        event = "unsupported_part_type",
                        part_kind = "data",
                        "Skipping A2A data part in ACP prompt (not supported)"
                    );
                    Ok(serde_json::json!(null))
                }
                "file" => {
                    // A2A file parts could map to ACP resource ContentBlocks
                    // For now, convert to text representation with warning
                    let mime_type = part
                        .get("mimeType")
                        .and_then(|m| m.as_str())
                        .unwrap_or("application/octet-stream");
                    tracing::warn!(
                        event = "unsupported_part_type",
                        part_kind = "file",
                        mime_type = %mime_type,
                        "A2A file part not fully supported in ACP - converting to text note"
                    );
                    Ok(serde_json::json!({
                        "type": "text",
                        "text": format!("[File attachment: {}]", mime_type)
                    }))
                }
                _ => Err(anyhow::anyhow!("Unsupported A2A part kind: {kind}")),
            }
        })
        .filter_map(|result| match result {
            Ok(Value::Null) => None, // Filter out null values from skipped data parts
            other => Some(other),
        })
        .collect()
}

/// Send session/prompt request and handle session/update notifications
/// Monitors cancellation channel for graceful shutdown
async fn send_session_prompt(
    client: &mut JsonRpcClient,
    notification_rx: &mut mpsc::UnboundedReceiver<JsonRpcMessage>,
    cancel_rx: &mut mpsc::UnboundedReceiver<()>,
    session_id: &str,
    task_payload: &Value,
    update_history: &mut Vec<Message>,
) -> Result<SessionPromptResult> {
    // Extract A2A message from task payload and translate to ACP ContentBlock array
    let message = task_payload.get("message").ok_or_else(|| {
        anyhow::anyhow!("task missing message field (expected A2A MessageSendParams)")
    })?;

    let parts = message
        .get("parts")
        .and_then(|p| p.as_array())
        .ok_or_else(|| anyhow::anyhow!("message missing parts array"))?;

    let prompt_parts = translate_a2a_parts_to_acp_content(parts)?;

    let prompt_params = SessionPromptParams {
        session_id: session_id.to_string(),
        prompt: prompt_parts,
    };

    let request_id = client
        .send_request("session/prompt", serde_json::to_value(&prompt_params)?)
        .await?;

    // Wait for response, handling session/update notifications, agent requests, and cancellation
    loop {
        tokio::select! {
            msg = notification_rx.recv() => {
                match msg {
                    Some(JsonRpcMessage::Response(resp)) if resp.id == request_id => {
                        if let Some(error) = resp.error {
                            return Ok(SessionPromptResult {
                                stop_reason: "error".to_string(),
                                error: Some(format!("{} (code: {})", error.message, error.code)),
                            });
                        }

                        let result: SessionPromptResult = serde_json::from_value(
                            resp.result
                                .ok_or_else(|| anyhow::anyhow!("session/prompt response missing result"))?,
                        )?;
                        return Ok(result);
                    }
                    Some(JsonRpcMessage::Response(_)) => {
                        // Different request ID, ignore
                    }
                    Some(JsonRpcMessage::Notification(notif)) => {
                        // Handle session/update notifications
                        if notif.method == "session/update" {
                            handle_session_update(&notif.params, update_history)?;
                        }
                    }
                    Some(JsonRpcMessage::Request(req)) => {
                        // Handle agent→worker requests
                        if req.method == "session/request_permission" {
                            handle_permission_request(client, &req.id, &req.params).await?;
                        } else {
                            tracing::warn!(method = %req.method, "Unsupported agent→worker request");
                            // Send JSON-RPC error response per ACP spec
                            client.send_error_response(
                                req.id,
                                -32601,
                                "Method not found".to_string()
                            ).await?;
                        }
                    }
                    Some(JsonRpcMessage::ParseError(line)) => {
                        // Malformed JSON-RPC response - fail task immediately
                        tracing::error!(
                            event = "parse_error",
                            phase = "session_prompt",
                            line = %line,
                            "Malformed JSON-RPC response during session/prompt"
                        );
                        return Err(anyhow::anyhow!(
                            "malformed JSON-RPC response during session/prompt: {line}"
                        ));
                    }
                    None => {
                        // Channel closed - subprocess died without sending response
                        tracing::error!(
                            event = "subprocess_closed",
                            phase = "session_prompt",
                            "Subprocess closed before session/prompt response"
                        );
                        return Err(anyhow::anyhow!(
                            "subprocess closed before session/prompt response"
                        ));
                    }
                }
            }
            Some(_) = cancel_rx.recv() => {
                // Cancellation requested - send session/cancel and wait for response
                tracing::info!(session_id = %session_id, "Sending session/cancel notification");

                let cancel_params = serde_json::json!({
                    "sessionId": session_id
                });

                client.send_notification("session/cancel", cancel_params).await?;

                // Wait up to CANCEL_GRACE_PERIOD_SECS for agent to respond with cancelled
                // Continue processing session/update and permission requests per ACP spec
                let grace_timeout = Duration::from_secs(CANCEL_GRACE_PERIOD_SECS);

                match timeout(grace_timeout, async {
                    while let Some(msg) = notification_rx.recv().await {
                        match msg {
                            JsonRpcMessage::Response(resp) if resp.id == request_id => {
                                return Some(resp);
                            }
                            JsonRpcMessage::Response(_) => {
                                // Different response, ignore
                            }
                            JsonRpcMessage::Notification(notif) => {
                                // Continue processing session/update per ACP spec
                                if notif.method == "session/update"
                                    && let Err(e) = handle_session_update(&notif.params, update_history)
                                {
                                    tracing::warn!(
                                        event = "session_update_error",
                                        phase = "cancellation",
                                        error = %e,
                                        "Failed to process session/update during cancellation"
                                    );
                                }
                            }
                            JsonRpcMessage::Request(req) => {
                                // MUST respond to permission requests with cancelled outcome per ACP spec
                                if req.method == "session/request_permission" {
                                    if let Err(e) = handle_permission_request_cancelled(client, &req.id).await {
                                        tracing::warn!(
                                            event = "permission_response_error",
                                            phase = "cancellation",
                                            error = %e,
                                            "Failed to respond to permission request during cancellation"
                                        );
                                    }
                                } else {
                                    // Other requests - send error response
                                    if let Err(e) = client.send_error_response(req.id, -32601, "Method not found".to_string()).await {
                                        tracing::warn!(
                                            event = "error_response_failed",
                                            phase = "cancellation",
                                            error = %e,
                                            "Failed to send error response during cancellation"
                                        );
                                    }
                                }
                            }
                            JsonRpcMessage::ParseError(_) => {
                                // Ignore malformed messages during grace period
                            }
                        }
                    }
                    None
                }).await {
                    Ok(Some(resp)) => {
                        // Check for JSON-RPC error first - don't mask agent failures
                        if let Some(error) = resp.error {
                            return Ok(SessionPromptResult {
                                stop_reason: "error".to_string(),
                                error: Some(format!("{} (code: {})", error.message, error.code)),
                            });
                        }

                        // Agent responded successfully within grace period
                        let result: SessionPromptResult = serde_json::from_value(
                            resp.result.ok_or_else(|| anyhow::anyhow!("session/prompt response missing result"))?
                        )?;
                        return Ok(result);
                    }
                    Ok(None) | Err(_) => {
                        // Agent didn't respond or timed out - force cancellation
                        tracing::warn!(
                            event = "cancellation_timeout",
                            grace_period_secs = CANCEL_GRACE_PERIOD_SECS,
                            "Agent did not respond to session/cancel within grace period"
                        );
                        return Ok(SessionPromptResult {
                            stop_reason: "cancelled".to_string(),
                            error: Some("Agent did not acknowledge cancellation".to_string()),
                        });
                    }
                }
            }
        }
    }
}

/// Handle session/update notification and convert to A2A Message
fn handle_session_update(params: &Value, update_history: &mut Vec<Message>) -> Result<()> {
    let update_params: SessionUpdateParams = serde_json::from_value(params.clone())?;

    // Convert all session update variants to A2A Message/Part
    let (role, text) = match &update_params.update {
        SessionUpdate::AgentMessageChunk { content } => {
            let text = content
                .get("text")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            ("agent", text)
        }
        SessionUpdate::UserMessageChunk { content } => {
            let text = content
                .get("text")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            ("user", text)
        }
        SessionUpdate::AgentThoughtChunk { content } => {
            let text = content
                .get("text")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            ("agent", format!("[thought] {text}"))
        }
        SessionUpdate::ToolCall {
            tool_call_id,
            title,
            ..
        } => (
            "agent",
            format!("[tool_call: {title} (id: {tool_call_id})]"),
        ),
        SessionUpdate::ToolCallUpdate {
            tool_call_id,
            title,
            status,
            ..
        } => {
            let title_str = title.as_deref().unwrap_or("unknown");
            let status_str = status.as_deref().unwrap_or("unknown");
            (
                "agent",
                format!(
                    "[tool_call_update: {title_str} (id: {tool_call_id}, status: {status_str})]"
                ),
            )
        }
        SessionUpdate::Plan { entries } => ("agent", format!("[plan] {} steps", entries.len())),
        SessionUpdate::AvailableCommandsUpdate { available_commands } => (
            "agent",
            format!("[available_commands] {} commands", available_commands.len()),
        ),
        SessionUpdate::CurrentModeUpdate { current_mode_id } => {
            ("agent", format!("[mode_change] {current_mode_id}"))
        }
    };

    let message = Message {
        message_id: Uuid::new_v4().to_string(),
        kind: "message".to_string(),
        role: role.to_string(),
        parts: vec![Part::Text { text }],
    };

    update_history.push(message);

    Ok(())
}

/// Auto-approve permission request and send response
async fn handle_permission_request(
    client: &mut JsonRpcClient,
    request_id: &str,
    params: &Value,
) -> Result<()> {
    let tool_call_id = params
        .get("toolCallId")
        .and_then(|t| t.as_str())
        .unwrap_or("unknown");

    // Log warning with tool ID (not full tool payload per observability contract)
    tracing::warn!(
        toolCallId = tool_call_id,
        "Auto-approved session/request_permission"
    );

    // Select first allow_once or allow_always option (not deny)
    let options = params
        .get("options")
        .and_then(|o| o.as_array())
        .ok_or_else(|| anyhow::anyhow!("permission request missing options"))?;

    let selected_option = options
        .iter()
        .find(|opt| {
            opt.get("kind")
                .and_then(|k| k.as_str())
                .map(|k| k == "allow_once" || k == "allow_always")
                .unwrap_or(false)
        })
        .ok_or_else(|| {
            anyhow::anyhow!("no allow_once/allow_always option found in permission request")
        })?;

    // Extract optionId from selected option
    let option_id = selected_option
        .get("optionId")
        .and_then(|id| id.as_str())
        .ok_or_else(|| anyhow::anyhow!("selected option missing optionId"))?;

    // Send JSON-RPC response (not notification!) per ACP spec
    let result = serde_json::json!({
        "outcome": {
            "outcome": "selected",
            "optionId": option_id
        }
    });

    client.send_response(request_id.to_string(), result).await?;

    Ok(())
}

/// Respond to permission request with cancelled outcome during cancellation
async fn handle_permission_request_cancelled(
    client: &mut JsonRpcClient,
    request_id: &str,
) -> Result<()> {
    let result = serde_json::json!({
        "outcome": {
            "outcome": "cancelled"
        }
    });

    client.send_response(request_id.to_string(), result).await?;

    Ok(())
}

/// Translate SessionPromptResult to TaskResult
fn translate_to_task_result(
    task: &TaskAssignment,
    prompt_result: SessionPromptResult,
    update_history: Vec<Message>,
) -> TaskResult {
    let task_status = match prompt_result.stop_reason.as_str() {
        "end_turn" => {
            // Agent's final response is already in update_history (came via session/update)
            // Extract text from last message for UI display
            let final_text = update_history
                .last()
                .and_then(|msg| msg.parts.first())
                .and_then(|part| match part {
                    Part::Text { text } => Some(text.as_str()),
                    _ => None,
                })
                .unwrap_or("");

            // Create completed status with both text (for UI) and history (for data)
            let mut status = A2ATaskStatus::completed();
            status.message = Some(Message {
                message_id: Uuid::new_v4().to_string(),
                kind: "message".to_string(),
                role: "agent".to_string(),
                parts: vec![
                    Part::Text {
                        text: final_text.to_string(),
                    },
                    Part::Data {
                        data: serde_json::json!({
                            "history": update_history
                        }),
                    },
                ],
            });
            status
        }
        "max_tokens" => {
            // Agent hit token limit - extract final text and add note
            let final_text = update_history
                .last()
                .and_then(|msg| msg.parts.first())
                .and_then(|part| match part {
                    Part::Text { text } => Some(text.as_str()),
                    _ => None,
                })
                .unwrap_or("");

            let text_with_note =
                format!("{final_text}\n\n(Note: Agent reached maximum token limit)");

            let mut status = A2ATaskStatus::completed();
            status.message = Some(Message {
                message_id: Uuid::new_v4().to_string(),
                kind: "message".to_string(),
                role: "agent".to_string(),
                parts: vec![
                    Part::Text {
                        text: text_with_note,
                    },
                    Part::Data {
                        data: serde_json::json!({
                            "history": update_history
                        }),
                    },
                ],
            });
            status
        }
        "max_turn_requests" => {
            // Agent hit request limit - extract final text and add note
            let final_text = update_history
                .last()
                .and_then(|msg| msg.parts.first())
                .and_then(|part| match part {
                    Part::Text { text } => Some(text.as_str()),
                    _ => None,
                })
                .unwrap_or("");

            let text_with_note =
                format!("{final_text}\n\n(Note: Agent reached maximum turn request limit)");

            let mut status = A2ATaskStatus::completed();
            status.message = Some(Message {
                message_id: Uuid::new_v4().to_string(),
                kind: "message".to_string(),
                role: "agent".to_string(),
                parts: vec![
                    Part::Text {
                        text: text_with_note,
                    },
                    Part::Data {
                        data: serde_json::json!({
                            "history": update_history
                        }),
                    },
                ],
            });
            status
        }
        "refusal" => {
            // Agent refused to continue - treat as failure
            A2ATaskStatus::failed("Agent refused to continue processing this request".to_string())
        }
        "cancelled" => A2ATaskStatus::canceled("Task cancelled by scheduler".to_string()),
        "error" => {
            // JSON-RPC protocol error - propagate error message from agent
            let error_text = prompt_result
                .error
                .unwrap_or_else(|| "Unknown protocol error".to_string());
            A2ATaskStatus::failed(error_text)
        }
        other => A2ATaskStatus::failed(format!("Invalid stop_reason from agent: {other}")),
    };

    TaskResult {
        execution_id: task.execution_id.clone(),
        node_id: task.node_id.clone(),
        task_status,
        artifacts: None,
    }
}

/// JSON-RPC client for sending requests/notifications over stdio
struct JsonRpcClient {
    stdin: ChildStdin,
}

impl JsonRpcClient {
    fn new(stdin: ChildStdin) -> Self {
        Self { stdin }
    }

    async fn send_request(&mut self, method: &str, params: Value) -> Result<String> {
        let request_id = Uuid::new_v4().to_string();

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: method.to_string(),
            params,
            id: request_id.clone(),
        };

        let json = serde_json::to_string(&request)?;
        self.stdin.write_all(json.as_bytes()).await?;
        self.stdin.write_all(b"\n").await?;
        self.stdin.flush().await?;

        Ok(request_id)
    }

    async fn send_notification(&mut self, method: &str, params: Value) -> Result<()> {
        let notification = JsonRpcNotification {
            jsonrpc: "2.0".to_string(),
            method: method.to_string(),
            params,
        };

        let json = serde_json::to_string(&notification)?;
        self.stdin.write_all(json.as_bytes()).await?;
        self.stdin.write_all(b"\n").await?;
        self.stdin.flush().await?;

        Ok(())
    }

    async fn send_response(&mut self, id: String, result: Value) -> Result<()> {
        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            result: Some(result),
            error: None,
            id,
        };

        let json = serde_json::to_string(&response)?;
        self.stdin.write_all(json.as_bytes()).await?;
        self.stdin.write_all(b"\n").await?;
        self.stdin.flush().await?;

        Ok(())
    }

    async fn send_error_response(&mut self, id: String, code: i64, message: String) -> Result<()> {
        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            result: None,
            error: Some(JsonRpcError {
                code,
                message,
                data: None,
            }),
            id,
        };

        let json = serde_json::to_string(&response)?;
        self.stdin.write_all(json.as_bytes()).await?;
        self.stdin.write_all(b"\n").await?;
        self.stdin.flush().await?;

        Ok(())
    }
}

/// JSON-RPC message enum for unified handling (bidirectional)
enum JsonRpcMessage {
    Response(JsonRpcResponse),
    Notification(JsonRpcNotification),
    Request(JsonRpcRequest), // Agent can send requests to worker (e.g., session/request_permission)
    ParseError(String),      // Malformed JSON-RPC that failed to parse
}

/// Background task to read JSON-RPC lines from subprocess stdout
async fn read_jsonrpc_lines(stdout: ChildStdout, tx: mpsc::UnboundedSender<JsonRpcMessage>) {
    let reader = BufReader::new(stdout);
    let mut lines = reader.lines();

    while let Ok(Some(line)) = lines.next_line().await {
        if line.trim().is_empty() {
            continue;
        }

        // Try parsing as request first (has id + method, from agent to worker)
        if let Ok(request) = serde_json::from_str::<JsonRpcRequest>(&line) {
            if tx.send(JsonRpcMessage::Request(request)).is_err() {
                break;
            }
        }
        // Try parsing as response (has id + result/error, from agent to worker)
        else if let Ok(response) = serde_json::from_str::<JsonRpcResponse>(&line) {
            if tx.send(JsonRpcMessage::Response(response)).is_err() {
                break;
            }
        }
        // Try parsing as notification (has method but no id)
        else if let Ok(notification) = serde_json::from_str::<JsonRpcNotification>(&line) {
            if tx.send(JsonRpcMessage::Notification(notification)).is_err() {
                break;
            }
        } else {
            // Malformed JSON-RPC - propagate error to protocol handlers to fail task
            tracing::warn!(
                event = "jsonrpc_parse_error",
                line = %line,
                "Failed to parse JSON-RPC message"
            );
            if tx.send(JsonRpcMessage::ParseError(line.clone())).is_err() {
                break;
            }
        }
    }
}

/// Cancel ACP task by triggering cancellation channel
///
/// Sends cancellation signal to the running ACP task execution, which will:
/// 1. Send session/cancel notification to the agent
/// 2. Wait up to 10 seconds for graceful shutdown
/// 3. Return cancelled status
///
/// The actual session/cancel protocol and grace period handling happens in send_session_prompt.
pub async fn cancel_acp_task(cancel_tx: &mpsc::UnboundedSender<()>) -> Result<()> {
    tracing::debug!("Triggering ACP task cancellation");
    cancel_tx
        .send(())
        .context("failed to send cancellation signal")?;
    Ok(())
}
