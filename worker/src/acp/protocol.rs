//! ACP protocol JSON-RPC types for worker ↔ agent communication.

use serde::{Deserialize, Serialize};

// JSON-RPC base types
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub method: String,
    pub params: serde_json::Value,
    pub id: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
    pub id: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcNotification {
    pub jsonrpc: String,
    pub method: String,
    pub params: serde_json::Value,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct JsonRpcError {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

// Initialize types
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct InitializeParams {
    #[serde(rename = "protocolVersion")]
    pub protocol_version: u32,
    #[serde(rename = "clientCapabilities")]
    pub client_capabilities: ClientCapabilities,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct ClientCapabilities {
    pub fs: Option<serde_json::Value>,
    pub terminal: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct InitializeResult {
    #[serde(rename = "protocolVersion")]
    pub protocol_version: u32,
    #[serde(rename = "agentCapabilities")]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_capabilities: Option<serde_json::Value>,
}

// Session types
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct SessionNewParams {
    pub cwd: String,
    #[serde(rename = "mcpServers")]
    pub mcp_servers: Vec<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct SessionNewResult {
    #[serde(rename = "sessionId")]
    pub session_id: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct SessionPromptParams {
    #[serde(rename = "sessionId")]
    pub session_id: String,
    pub prompt: Vec<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct SessionPromptResult {
    #[serde(rename = "stopReason")]
    pub stop_reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct SessionCancelParams {
    #[serde(rename = "sessionId")]
    pub session_id: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_initialize_params_deserialization_from_contract() {
        let json_str = r#"{
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": null,
                "terminal": null
            }
        }"#;

        let params: InitializeParams = serde_json::from_str(json_str).unwrap();

        assert_eq!(params.protocol_version, 1);
        assert!(params.client_capabilities.fs.is_none());
        assert!(params.client_capabilities.terminal.is_none());
    }

    #[test]
    fn test_session_new_params_deserialization_from_contract() {
        let json_str = r#"{
            "cwd": "/absolute/path/to/workdir",
            "mcpServers": []
        }"#;

        let params: SessionNewParams = serde_json::from_str(json_str).unwrap();

        assert_eq!(params.cwd, "/absolute/path/to/workdir");
        assert!(params.mcp_servers.is_empty());
    }
}
