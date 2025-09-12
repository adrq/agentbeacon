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
        #[allow(dead_code)]
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
    #[allow(dead_code)]
    pub command: String,
    #[serde(default)]
    #[allow(dead_code)]
    pub args: Vec<String>,
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

    Ok(config)
}
