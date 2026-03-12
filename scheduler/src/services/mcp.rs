use crate::db::project_mcp_servers::McpServerPoolEntry;
use serde_json::Value as JsonValue;

/// Build the mcp_servers payload for a task_payload from project MCP server entries.
/// Each server becomes a key in the returned JSON object, with `type` injected from `transport_type`.
pub fn build_mcp_servers_payload(servers: &[McpServerPoolEntry]) -> JsonValue {
    let map: serde_json::Map<String, JsonValue> = servers
        .iter()
        .map(|s| {
            let mut config = s.config.clone();
            config["type"] = JsonValue::String(s.transport_type.clone());
            (s.name.clone(), config)
        })
        .collect();
    map.into()
}
