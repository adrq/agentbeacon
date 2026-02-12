//! ACP executor adapter wrapping existing ACP subprocess management.
//!
//! Implements the AgentHandle trait by delegating to the existing ACP
//! protocol functions in worker::acp::executor.

use anyhow::{Context, Result};
use async_trait::async_trait;
use common::Message;
use serde::Deserialize;
use std::collections::HashMap;
use std::time::Duration;
use tokio::sync::mpsc;

use super::{AgentHandle, SessionConfig, TurnResult};
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

pub struct AcpAgentHandle {
    client: Option<JsonRpcClient>,
    notification_rx: mpsc::UnboundedReceiver<JsonRpcMessage>,
    prompt_cancel_tx: Option<mpsc::UnboundedSender<()>>,
    session_id: String,
    child: tokio::process::Child,
    reader_handle: tokio::task::JoinHandle<()>,
}

impl AcpAgentHandle {
    pub async fn start(config: SessionConfig) -> Result<Self> {
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
            send_session_new(&mut client, &mut notification_rx, &config.cwd),
        )
        .await
        .context("session/new timed out")??;

        tracing::info!(
            session_id = %config.session_id,
            acp_session_id = %acp_session_id,
            "ACP agent started"
        );

        Ok(Self {
            client: Some(client),
            notification_rx,
            prompt_cancel_tx: None,
            session_id: acp_session_id,
            child,
            reader_handle,
        })
    }
}

#[async_trait]
impl AgentHandle for AcpAgentHandle {
    async fn send_prompt(&mut self, task_payload: &serde_json::Value) -> Result<TurnResult> {
        let client = self.client.as_mut().context("ACP client already stopped")?;

        // Create a fresh cancel channel for this prompt
        let (cancel_tx, mut cancel_rx) = mpsc::unbounded_channel();
        self.prompt_cancel_tx = Some(cancel_tx);

        // Extract prompt content: Object with message.parts (A2A) or String (plain text)
        let prompt_parts = if let Some(message) = task_payload.get("message") {
            let parts = message
                .get("parts")
                .and_then(|p| p.as_array())
                .ok_or_else(|| anyhow::anyhow!("message missing parts array"))?;
            translate_a2a_parts_to_acp_content(parts)?
        } else if let Some(text) = task_payload.as_str() {
            vec![serde_json::json!({"type": "text", "text": text})]
        } else {
            return Err(anyhow::anyhow!(
                "unsupported task_payload format: expected object with message or string"
            ));
        };

        let mut update_history: Vec<Message> = Vec::new();
        let prompt_result = send_session_prompt(
            client,
            &mut self.notification_rx,
            &mut cancel_rx,
            &self.session_id,
            prompt_parts,
            &mut update_history,
        )
        .await?;

        let success = prompt_result.stop_reason != "error"
            && prompt_result.stop_reason != "cancelled"
            && prompt_result.stop_reason != "refusal";

        let error = if success {
            None
        } else {
            Some(
                prompt_result
                    .error
                    .unwrap_or_else(|| prompt_result.stop_reason.clone()),
            )
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

        Ok(TurnResult {
            agent_session_id: Some(self.session_id.clone()),
            success,
            error,
            output,
        })
    }

    async fn cancel(&mut self) -> Result<()> {
        if let Some(tx) = &self.prompt_cancel_tx {
            let _ = tx.send(());
        }
        Ok(())
    }

    async fn stop(&mut self) -> Result<()> {
        // Drop client to close stdin
        self.prompt_cancel_tx = None;
        self.client = None;
        terminate_subprocess(&mut self.child).await;
        let _ = (&mut self.reader_handle).await;
        Ok(())
    }
}
