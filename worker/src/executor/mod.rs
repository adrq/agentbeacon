pub mod acp;

use anyhow::Result;
use async_trait::async_trait;

#[allow(dead_code)]
pub struct SessionConfig {
    pub session_id: String,
    pub execution_id: String,
    pub agent_type: String,
    pub agent_config: serde_json::Value,
    pub sandbox_config: serde_json::Value,
    pub cwd: String,
}

#[allow(dead_code)]
pub struct TurnResult {
    pub agent_session_id: Option<String>,
    pub success: bool,
    pub error: Option<String>,
    pub output: Option<serde_json::Value>,
}

#[async_trait]
pub trait AgentHandle: Send {
    /// Send prompt to running agent. task_payload shapes:
    /// - Initial/delegate: JSON object {agent_id, agent_type, agent_config, sandbox_config, message}
    /// - User answer: plain string "[user]\n\n{text}"
    /// - Handoff result: plain string "[delegated result from X · session Y]\n\n{text}"
    /// Adapter detects: if Value::Object → extract message field; if Value::String → use as prompt text
    async fn send_prompt(&mut self, task_payload: &serde_json::Value) -> Result<TurnResult>;
    async fn cancel(&mut self) -> Result<()>;
    async fn stop(&mut self) -> Result<()>;
}

/// Factory: create AgentHandle based on agent_type from task payload.
pub async fn start_executor(config: SessionConfig) -> Result<Box<dyn AgentHandle>> {
    match config.agent_type.as_str() {
        "acp" => Ok(Box::new(acp::AcpAgentHandle::start(config).await?)),
        other => Err(anyhow::anyhow!("unsupported agent_type: {other}")),
    }
}
