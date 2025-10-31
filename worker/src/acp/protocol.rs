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

// Notification types
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct SessionUpdateParams {
    #[serde(rename = "sessionId")]
    pub session_id: String,
    pub update: SessionUpdate,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "sessionUpdate", rename_all = "snake_case")]
pub enum SessionUpdate {
    #[serde(rename = "user_message_chunk")]
    UserMessageChunk { content: serde_json::Value },

    #[serde(rename = "agent_message_chunk")]
    AgentMessageChunk { content: serde_json::Value },

    #[serde(rename = "agent_thought_chunk")]
    AgentThoughtChunk { content: serde_json::Value },

    #[serde(rename = "tool_call")]
    ToolCall {
        #[serde(rename = "toolCallId")]
        tool_call_id: String,
        title: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        content: Option<Vec<serde_json::Value>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        status: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        kind: Option<String>,
    },

    #[serde(rename = "tool_call_update")]
    ToolCallUpdate {
        #[serde(rename = "toolCallId")]
        tool_call_id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        title: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        content: Option<Vec<serde_json::Value>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        status: Option<String>,
    },

    #[serde(rename = "plan")]
    Plan { entries: Vec<serde_json::Value> },

    #[serde(rename = "available_commands_update")]
    AvailableCommandsUpdate {
        #[serde(rename = "availableCommands")]
        available_commands: Vec<serde_json::Value>,
    },

    #[serde(rename = "current_mode_update")]
    CurrentModeUpdate {
        #[serde(rename = "currentModeId")]
        current_mode_id: String,
    },
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

    #[test]
    fn test_session_update_params_with_nested_update() {
        let json_str = r#"{
            "sessionId": "sess-abc123",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": "Partial response text..."
                }
            }
        }"#;

        let params: SessionUpdateParams = serde_json::from_str(json_str).unwrap();

        assert_eq!(params.session_id, "sess-abc123");
        match &params.update {
            SessionUpdate::AgentMessageChunk { content } => {
                assert_eq!(
                    content.get("text").and_then(|t| t.as_str()),
                    Some("Partial response text...")
                );
            }
            _ => panic!("Expected AgentMessageChunk variant"),
        }
    }

    #[test]
    fn test_session_update_plan_without_content() {
        let json_str = r#"{
            "sessionId": "sess-456",
            "update": {
                "sessionUpdate": "plan",
                "entries": [
                    {"id": "step-1", "title": "Analyze requirements"}
                ]
            }
        }"#;

        let params: SessionUpdateParams = serde_json::from_str(json_str).unwrap();

        assert_eq!(params.session_id, "sess-456");
        match &params.update {
            SessionUpdate::Plan { entries } => {
                assert_eq!(entries.len(), 1);
            }
            _ => panic!("Expected Plan variant"),
        }
    }

    #[test]
    fn test_session_update_tool_call_without_content() {
        let json_str = r#"{
            "sessionId": "sess-789",
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "tool-123",
                "title": "Read file"
            }
        }"#;

        let params: SessionUpdateParams = serde_json::from_str(json_str).unwrap();

        assert_eq!(params.session_id, "sess-789");
        match &params.update {
            SessionUpdate::ToolCall {
                tool_call_id,
                title,
                content,
                ..
            } => {
                assert_eq!(tool_call_id, "tool-123");
                assert_eq!(title, "Read file");
                assert!(content.is_none());
            }
            _ => panic!("Expected ToolCall variant"),
        }
    }
}
