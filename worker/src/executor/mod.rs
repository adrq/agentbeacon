pub mod acp;
pub mod claude;

use anyhow::Result;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    ExecutorFailed,
    Cancelled,
    BudgetExceeded,
    MaxTurns,
}

impl ErrorKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            ErrorKind::ExecutorFailed => "executor_failed",
            ErrorKind::Cancelled => "cancelled",
            ErrorKind::BudgetExceeded => "budget_exceeded",
            ErrorKind::MaxTurns => "max_turns",
        }
    }
}

#[allow(dead_code)]
pub struct SessionConfig {
    pub session_id: String,
    pub execution_id: String,
    pub agent_type: String,
    pub agent_config: serde_json::Value,
    pub sandbox_config: serde_json::Value,
    pub cwd: String,
    pub scheduler_url: String,
    /// Override for Node.js binary path (Claude executor)
    pub node_path: Option<String>,
    /// Override for executors/dist directory (Claude executor)
    pub executors_dir: Option<String>,
}

pub struct TurnResult {
    pub agent_session_id: Option<String>,
    pub error: Option<String>,
    pub error_kind: Option<ErrorKind>,
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
        "claude_sdk" => Ok(Box::new(claude::ClaudeAgentHandle::start(config).await?)),
        other => Err(anyhow::anyhow!("unsupported agent_type: {other}")),
    }
}
