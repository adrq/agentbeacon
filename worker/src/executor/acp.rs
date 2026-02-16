//! ACP executor adapter wrapping existing ACP subprocess management.
//!
//! Background task pattern: `start()` initializes the ACP subprocess (JSON-RPC
//! initialize + session/new) then spawns a background task that processes
//! commands sequentially. Mid-turn Prompt commands are buffered until the
//! current turn completes (ACP doesn't support concurrent JSON-RPC prompts).

use anyhow::{Context, Result};
use common::Message;
use serde::Deserialize;
use std::collections::HashMap;
use std::time::Duration;
use tokio::sync::mpsc;

use super::{AgentCommand, AgentEvent, ErrorKind, ExecutorHandle, SessionConfig, TurnResult};
use crate::acp::executor::{
    JsonRpcClient, JsonRpcMessage, LegacyAcpConfig, read_jsonrpc_lines, send_initialize,
    send_session_new, send_session_prompt, spawn_acp_subprocess, terminate_subprocess,
    translate_a2a_parts_to_acp_content,
};

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

    // Background task: processes commands sequentially
    let task_handle = tokio::spawn(background_task(
        child,
        client,
        notification_rx,
        reader_handle,
        cmd_rx,
        event_tx,
        acp_session_id,
    ));

    Ok(ExecutorHandle {
        cmd_tx,
        event_rx,
        task_handle,
    })
}

/// Background task that processes ACP commands sequentially.
///
/// ACP doesn't support concurrent JSON-RPC requests during a prompt, so
/// commands are processed one at a time. Cancel is delivered via a separate
/// channel that `send_session_prompt` monitors.
async fn background_task(
    mut child: tokio::process::Child,
    mut client: JsonRpcClient,
    mut notification_rx: mpsc::UnboundedReceiver<JsonRpcMessage>,
    reader_handle: tokio::task::JoinHandle<()>,
    mut cmd_rx: mpsc::UnboundedReceiver<AgentCommand>,
    event_tx: mpsc::UnboundedSender<AgentEvent>,
    session_id: String,
) {
    let mut prompt_cancel_tx: Option<mpsc::UnboundedSender<()>> = None;

    loop {
        let cmd = match cmd_rx.recv().await {
            Some(c) => c,
            None => {
                // cmd_rx closed — worker dropped cmd_tx, shut down
                terminate_subprocess(&mut child).await;
                // Abort reader: `uv run` process trees may keep stdout open
                // after the parent is killed, so EOF never arrives.
                reader_handle.abort();
                return;
            }
        };

        match cmd {
            AgentCommand::Start(task_payload) => {
                run_acp_prompt(
                    &mut client,
                    &mut notification_rx,
                    &event_tx,
                    &session_id,
                    &task_payload,
                    &mut prompt_cancel_tx,
                )
                .await;
            }
            AgentCommand::Prompt(text) => {
                let task_payload = serde_json::Value::String(text);
                run_acp_prompt(
                    &mut client,
                    &mut notification_rx,
                    &event_tx,
                    &session_id,
                    &task_payload,
                    &mut prompt_cancel_tx,
                )
                .await;
            }
            AgentCommand::Cancel => {
                if let Some(tx) = &prompt_cancel_tx {
                    let _ = tx.send(());
                }
            }
            AgentCommand::Stop => {
                drop(prompt_cancel_tx);
                terminate_subprocess(&mut child).await;
                reader_handle.abort();
                return;
            }
        }
    }
}

/// Run a single ACP prompt turn and emit the result as AgentEvent::TurnComplete.
async fn run_acp_prompt(
    client: &mut JsonRpcClient,
    notification_rx: &mut mpsc::UnboundedReceiver<JsonRpcMessage>,
    event_tx: &mpsc::UnboundedSender<AgentEvent>,
    session_id: &str,
    task_payload: &serde_json::Value,
    prompt_cancel_tx: &mut Option<mpsc::UnboundedSender<()>>,
) {
    // Create a fresh cancel channel for this prompt
    let (cancel_tx, mut cancel_rx) = mpsc::unbounded_channel();
    *prompt_cancel_tx = Some(cancel_tx);

    // Extract prompt content: Object with message.parts (A2A) or String (plain text)
    let prompt_parts = if let Some(message) = task_payload.get("message") {
        let parts = match message.get("parts").and_then(|p| p.as_array()) {
            Some(p) => p,
            None => {
                let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                    agent_session_id: Some(session_id.to_string()),
                    error: Some("message missing parts array".into()),
                    error_kind: Some(ErrorKind::ExecutorFailed),
                    output: None,
                }));
                return;
            }
        };
        match translate_a2a_parts_to_acp_content(parts) {
            Ok(p) => p,
            Err(e) => {
                let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                    agent_session_id: Some(session_id.to_string()),
                    error: Some(format!("failed to translate parts: {e}")),
                    error_kind: Some(ErrorKind::ExecutorFailed),
                    output: None,
                }));
                return;
            }
        }
    } else if let Some(text) = task_payload.as_str() {
        vec![serde_json::json!({"type": "text", "text": text})]
    } else {
        let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
            agent_session_id: Some(session_id.to_string()),
            error: Some("unsupported task_payload format".into()),
            error_kind: Some(ErrorKind::ExecutorFailed),
            output: None,
        }));
        return;
    };

    let mut update_history: Vec<Message> = Vec::new();
    let prompt_result = send_session_prompt(
        client,
        notification_rx,
        &mut cancel_rx,
        session_id,
        prompt_parts,
        &mut update_history,
    )
    .await;

    match prompt_result {
        Ok(prompt_result) => {
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

            // Consolidate agent-role messages into a single output value
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

            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                agent_session_id: Some(session_id.to_string()),
                error,
                error_kind,
                output,
            }));
        }
        Err(e) => {
            let _ = event_tx.send(AgentEvent::TurnComplete(TurnResult {
                agent_session_id: Some(session_id.to_string()),
                error: Some(format!("{e:#}")),
                error_kind: Some(ErrorKind::ExecutorFailed),
                output: None,
            }));
        }
    }
}
