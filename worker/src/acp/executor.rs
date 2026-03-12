//! ACP protocol executor for worker ↔ agent communication over stdio.
//!
//! Core protocol functions used by executor::acp::AcpAgentHandle.

use anyhow::{Context, Result};
use common::{Message, Part};
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::panic::catch_unwind;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::mpsc;
use tokio::time::timeout;
use uuid::Uuid;

use super::protocol::*;

/// ACP agent config shape used by spawn_acp_subprocess
#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)]
pub(crate) struct LegacyAcpConfig {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub timeout: Option<u64>,
    pub env: Option<HashMap<String, String>>,
}

/// Spawn ACP subprocess with stdio communication
pub(crate) fn spawn_acp_subprocess(acp_config: &LegacyAcpConfig) -> Result<Child> {
    let mut cmd = Command::new(&acp_config.command);
    cmd.args(&acp_config.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
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

/// Terminate subprocess: wait 1s for natural exit, SIGTERM + 1s, then SIGKILL.
pub(crate) async fn terminate_subprocess(child: &mut Child) {
    // Wait 1s for natural exit
    match timeout(Duration::from_secs(1), child.wait()).await {
        Ok(Ok(status)) => {
            tracing::debug!(%status, "Subprocess exited naturally");
            return;
        }
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "Error waiting for subprocess");
            return;
        }
        Err(_) => {}
    }

    // Try SIGTERM
    if let Some(pid) = child.id() {
        tracing::debug!(pid, "Sending SIGTERM to subprocess");
        // Safety: pid is a valid process ID from child.id()
        unsafe { libc::kill(pid as i32, libc::SIGTERM) };

        match timeout(Duration::from_secs(1), child.wait()).await {
            Ok(Ok(status)) => {
                tracing::debug!(%status, "Subprocess exited after SIGTERM");
                return;
            }
            Ok(Err(e)) => {
                tracing::warn!(error = %e, "Error waiting for subprocess after SIGTERM");
                return;
            }
            Err(_) => {
                tracing::warn!(
                    pid,
                    "Subprocess did not exit after SIGTERM, sending SIGKILL"
                );
            }
        }
    }

    let _ = child.kill().await;
}

/// Send initialize request and wait for response
pub(crate) async fn send_initialize(
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
            JsonRpcMessage::Response(_) => {}
            JsonRpcMessage::Notification(_) => {}
            JsonRpcMessage::Request(req) => {
                tracing::warn!(
                    event = "unexpected_request",
                    method = %req.method,
                    phase = "initialize",
                    "Unexpected agent request during initialize"
                );
            }
            JsonRpcMessage::ParseError(line) => {
                let truncated: String = line.chars().take(80).collect();
                tracing::error!(
                    event = "parse_error",
                    phase = "initialize",
                    line_len = line.len(),
                    line_preview = %truncated,
                    "Malformed JSON-RPC response during initialize"
                );
                return Err(anyhow::anyhow!(
                    "malformed JSON-RPC response during initialize"
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
pub(crate) async fn send_session_new(
    client: &mut JsonRpcClient,
    notification_rx: &mut mpsc::UnboundedReceiver<JsonRpcMessage>,
    cwd: &str,
    scheduler_url: &str,
    session_id: &str,
) -> Result<String> {
    // TODO(KI-104): merge SessionConfig.user_mcp_servers here (needs format
    // translation — ACP uses array of {name, type, url, headers:[{name,value}]},
    // not the keyed-object Claude Desktop format used by the SDK path).
    let session_params = SessionNewParams {
        cwd: cwd.to_string(),
        mcp_servers: vec![serde_json::json!({
            "type": "http",
            "name": "beacon-coordinator",
            "url": format!("{}/mcp", scheduler_url.trim_end_matches('/')),
            "headers": [
                {"name": "Authorization", "value": format!("Bearer {}", session_id)}
            ]
        })],
    };

    let request_id = client
        .send_request("session/new", serde_json::to_value(&session_params)?)
        .await?;

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
            JsonRpcMessage::Response(_) => {}
            JsonRpcMessage::Notification(_) => {}
            JsonRpcMessage::Request(req) => {
                tracing::warn!(
                    event = "unexpected_request",
                    method = %req.method,
                    phase = "session_new",
                    "Unexpected agent request during session/new"
                );
            }
            JsonRpcMessage::ParseError(line) => {
                let truncated: String = line.chars().take(80).collect();
                tracing::error!(
                    event = "parse_error",
                    phase = "session_new",
                    line_len = line.len(),
                    line_preview = %truncated,
                    "Malformed JSON-RPC response during session/new"
                );
                return Err(anyhow::anyhow!(
                    "malformed JSON-RPC response during session/new"
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
pub(crate) fn translate_a2a_parts_to_acp_content(parts: &[Value]) -> Result<Vec<Value>> {
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
                    tracing::warn!(
                        event = "unsupported_part_type",
                        part_kind = "data",
                        "Skipping A2A data part in ACP prompt (not supported)"
                    );
                    Ok(serde_json::json!(null))
                }
                "file" => {
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
            Ok(Value::Null) => None,
            other => Some(other),
        })
        .collect()
}

/// Handle session/update notification and convert to A2A Message.
///
/// Raw passthrough: non-chunk variants pass through as Part::Data with only
/// `sessionUpdate` renamed to `type`. Chunk variants unwrap the ContentBlock
/// wrapper — message chunks become Part::Text, thought chunks become Part::Data
/// with extracted text.
pub(crate) fn handle_session_update(
    params: &Value,
    update_history: &mut Vec<Message>,
) -> Result<()> {
    let update = params
        .get("update")
        .ok_or_else(|| anyhow::anyhow!("session/update missing update"))?;

    let variant = update
        .get("sessionUpdate")
        .and_then(|s| s.as_str())
        .ok_or_else(|| anyhow::anyhow!("session/update missing sessionUpdate discriminator"))?;

    let (role, parts) = match variant {
        // Text chunks: extract from ContentBlock wrapper → Part::Text
        "agent_message_chunk" | "user_message_chunk" => {
            let text = update
                .get("content")
                .and_then(|c| c.get("text"))
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            let role = if variant == "user_message_chunk" {
                "user"
            } else {
                "agent"
            };
            (role, vec![Part::Text { text }])
        }
        // Thought chunks: extract text, keep as Data with spec discriminator
        "agent_thought_chunk" => {
            let text = update
                .get("content")
                .and_then(|c| c.get("text"))
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            (
                "agent",
                vec![Part::Data {
                    data: serde_json::json!({
                        "type": "agent_thought_chunk",
                        "text": text
                    }),
                }],
            )
        }
        // Everything else: raw passthrough, rename sessionUpdate → type
        _ => {
            let mut data = update.clone();
            if let Some(obj) = data.as_object_mut()
                && let Some(val) = obj.remove("sessionUpdate")
            {
                obj.insert("type".to_string(), val);
            }
            ("agent", vec![Part::Data { data }])
        }
    };

    update_history.push(Message {
        message_id: Uuid::new_v4().to_string(),
        kind: "message".to_string(),
        role: role.to_string(),
        parts,
    });
    Ok(())
}

/// Auto-approve permission request and send response
pub(crate) async fn handle_permission_request(
    client: &mut JsonRpcClient,
    request_id: &str,
    params: &Value,
) -> Result<()> {
    let tool_call_id = params
        .get("toolCallId")
        .and_then(|t| t.as_str())
        .unwrap_or("unknown");

    tracing::warn!(
        toolCallId = tool_call_id,
        "Auto-approved session/request_permission"
    );

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

    let option_id = selected_option
        .get("optionId")
        .and_then(|id| id.as_str())
        .ok_or_else(|| anyhow::anyhow!("selected option missing optionId"))?;

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
pub(crate) async fn handle_permission_request_cancelled(
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

/// JSON-RPC client for sending requests/notifications over stdio
pub(crate) struct JsonRpcClient {
    stdin: ChildStdin,
}

impl JsonRpcClient {
    pub(crate) fn new(stdin: ChildStdin) -> Self {
        Self { stdin }
    }

    pub(crate) async fn send_request(&mut self, method: &str, params: Value) -> Result<String> {
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

    pub(crate) async fn send_notification(&mut self, method: &str, params: Value) -> Result<()> {
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

    pub(crate) async fn send_error_response(
        &mut self,
        id: String,
        code: i64,
        message: String,
    ) -> Result<()> {
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
pub(crate) enum JsonRpcMessage {
    Response(JsonRpcResponse),
    Notification(JsonRpcNotification),
    Request(JsonRpcRequest),
    ParseError(String),
}

/// Background task to read JSON-RPC lines from subprocess stdout
pub(crate) async fn read_jsonrpc_lines(
    stdout: ChildStdout,
    tx: mpsc::UnboundedSender<JsonRpcMessage>,
) {
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
            let truncated: String = line.chars().take(80).collect();
            tracing::warn!(
                event = "jsonrpc_parse_error",
                line_len = line.len(),
                line_preview = %truncated,
                "Failed to parse JSON-RPC message"
            );
            if tx.send(JsonRpcMessage::ParseError(line.clone())).is_err() {
                break;
            }
        }
    }
}
