//! Agent configuration deserialization for A2A, ACP, and Stdio agent types.

use anyhow::{Context, Result};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Deserialize, Clone)]
pub struct AgentsConfig {
    pub agents: HashMap<String, AgentConfig>,
}

#[derive(Debug, Deserialize, Clone)]
#[serde(rename_all = "lowercase", tag = "type")]
pub enum AgentConfig {
    A2a {
        config: A2aConfig,
    },
    Stdio {
        #[allow(dead_code)]
        config: StdioConfig,
    },
    Acp {
        config: AcpConfig,
    },
}

#[derive(Debug, Deserialize, Clone)]
pub struct A2aConfig {
    pub url: String,
}

#[derive(Debug, Deserialize, Clone)]
pub struct StdioConfig {
    #[allow(dead_code)]
    pub command: String,
    #[serde(default)]
    #[allow(dead_code)]
    pub args: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct AcpConfig {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub timeout: Option<u64>,
    pub env: Option<HashMap<String, String>>,
}

impl AcpConfig {
    /// Validates ACP configuration constraints that serde cannot enforce
    /// (empty strings and zero values that are syntactically valid but semantically invalid).
    pub fn validate(&self) -> Result<()> {
        if self.command.is_empty() {
            anyhow::bail!("ACP agent command cannot be empty");
        }
        if let Some(timeout) = self.timeout {
            if timeout == 0 {
                anyhow::bail!("ACP agent timeout must be greater than 0");
            }
        }
        Ok(())
    }
}

pub fn load_agents_config(path: &Path) -> Result<AgentsConfig> {
    let content = std::fs::read_to_string(path).context(format!(
        "failed to read agents config file: {}",
        path.display()
    ))?;

    let value: serde_json::Value = serde_yaml::from_str(&content).context(format!(
        "failed to parse agents config YAML from: {}",
        path.display()
    ))?;

    common::validate_agents_config(&value).context(format!(
        "agents config validation failed for: {}",
        path.display()
    ))?;

    let config: AgentsConfig = serde_json::from_value(value).context(format!(
        "failed to deserialize agents config from: {}",
        path.display()
    ))?;

    for (name, agent_config) in &config.agents {
        if let AgentConfig::Acp { config: acp_config } = agent_config {
            acp_config
                .validate()
                .context(format!("ACP agent '{name}' validation failed"))?;
        }
    }

    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_acp_config_minimal_valid() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent"
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                assert_eq!(config.command, "/usr/bin/agent");
                assert_eq!(config.args.len(), 0);
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_with_all_fields() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "args": ["--model", "claude-3-sonnet"],
                "timeout": 60,
                "env": {
                    "LOG_LEVEL": "debug"
                }
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                assert_eq!(config.command, "/usr/bin/agent");
                assert_eq!(config.args, vec!["--model", "claude-3-sonnet"]);
                assert_eq!(config.timeout, Some(60));
                assert!(config.env.is_some());
                let env = config.env.unwrap();
                assert_eq!(env.get("LOG_LEVEL"), Some(&"debug".to_string()));
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_with_timeout_only() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "timeout": 120
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                assert_eq!(config.timeout, Some(120));
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_missing_command() {
        let json = json!({
            "type": "acp",
            "config": {
                "timeout": 60
            }
        });

        let result: Result<AgentConfig, _> = serde_json::from_value(json);
        assert!(result.is_err());
    }

    #[test]
    fn test_acp_config_empty_command() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": ""
            }
        });

        let result: Result<AgentConfig, _> = serde_json::from_value(json);
        assert!(result.is_ok());
    }

    #[test]
    fn test_acp_config_zero_timeout() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "timeout": 0
            }
        });

        let result: Result<AgentConfig, _> = serde_json::from_value(json);
        assert!(result.is_ok());
    }

    #[test]
    fn test_acp_config_negative_timeout() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "timeout": -1
            }
        });

        let result: Result<AgentConfig, _> = serde_json::from_value(json);
        assert!(result.is_err());
    }

    #[test]
    fn test_mixed_agent_configs() {
        let json = json!({
            "agents": {
                "a2a-agent": {
                    "type": "a2a",
                    "config": {
                        "url": "http://localhost:8080"
                    }
                },
                "acp-agent": {
                    "type": "acp",
                    "config": {
                        "command": "/usr/bin/acp-agent",
                        "timeout": 30
                    }
                }
            }
        });

        let config: AgentsConfig = serde_json::from_value(json).unwrap();
        assert_eq!(config.agents.len(), 2);

        match config.agents.get("a2a-agent") {
            Some(AgentConfig::A2a { .. }) => {}
            _ => panic!("Expected a2a-agent to be A2a variant"),
        }

        match config.agents.get("acp-agent") {
            Some(AgentConfig::Acp { config }) => {
                assert_eq!(config.command, "/usr/bin/acp-agent");
                assert_eq!(config.timeout, Some(30));
            }
            _ => panic!("Expected acp-agent to be Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_with_args_array() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "args": []
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                assert_eq!(config.args.len(), 0);
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_with_env_vars() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "env": {
                    "VAR_1": "value1",
                    "VAR_2": "value2"
                }
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                let env = config.env.unwrap();
                assert_eq!(env.len(), 2);
                assert_eq!(env.get("VAR_1"), Some(&"value1".to_string()));
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_validate_empty_command() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": ""
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                let result = config.validate();
                assert!(result.is_err());
                assert!(result.unwrap_err().to_string().contains("empty"));
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_validate_zero_timeout() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "timeout": 0
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                let result = config.validate();
                assert!(result.is_err());
                assert!(result.unwrap_err().to_string().contains("timeout"));
            }
            _ => panic!("Expected Acp variant"),
        }
    }

    #[test]
    fn test_acp_config_validate_valid() {
        let json = json!({
            "type": "acp",
            "config": {
                "command": "/usr/bin/agent",
                "timeout": 30
            }
        });

        let config: AgentConfig = serde_json::from_value(json).unwrap();
        match config {
            AgentConfig::Acp { config } => {
                assert!(config.validate().is_ok());
            }
            _ => panic!("Expected Acp variant"),
        }
    }
}
