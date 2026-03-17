use axum::{Json, Router, response::IntoResponse, routing::get};
use serde::{Deserialize, Serialize};

use crate::app::AppState;

/// A2A v1.0 Agent Interface
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentInterface {
    pub url: String,
    pub protocol_binding: String,
    pub protocol_version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tenant: Option<String>,
}

/// A2A v1.0 Capabilities
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Capabilities {
    pub streaming: bool,
    pub push_notifications: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extended_agent_card: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extensions: Option<Vec<serde_json::Value>>,
}

/// A2A v1.0 Skill
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Skill {
    pub id: String,
    pub name: String,
    pub description: String,
    pub input_modes: Vec<String>,
    pub output_modes: Vec<String>,
}

/// A2A v1.0 Agent Card
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentCard {
    pub name: String,
    pub version: String,
    pub description: String,
    pub default_input_modes: Vec<String>,
    pub default_output_modes: Vec<String>,
    pub capabilities: Capabilities,
    pub skills: Vec<Skill>,
    pub supported_interfaces: Vec<AgentInterface>,
}

impl AgentCard {
    /// Create A2A v1.0 compliant agent card for scheduler
    pub fn new(base_url: &str) -> Self {
        let rpc_url = format!("{}/rpc", base_url.trim_end_matches('/'));
        Self {
            name: "AgentBeacon Scheduler".to_string(),
            version: "1.0.0".to_string(),
            description: "Multi-agent orchestrator".to_string(),
            default_input_modes: vec![
                "application/json".to_string(),
                "text/plain".to_string(),
            ],
            default_output_modes: vec!["application/json".to_string()],
            capabilities: Capabilities {
                streaming: false,
                push_notifications: false,
                extended_agent_card: None,
                extensions: None,
            },
            skills: vec![Skill {
                id: "agent-coordination".to_string(),
                name: "Agent Coordination".to_string(),
                description: "Coordinate multiple AI agents via lead-agent delegation with session-based execution tracking.".to_string(),
                input_modes: vec![
                    "application/json".to_string(),
                    "text/plain".to_string(),
                ],
                output_modes: vec!["application/json".to_string()],
            }],
            supported_interfaces: vec![AgentInterface {
                url: rpc_url,
                protocol_binding: "JSONRPC".to_string(),
                protocol_version: "1.0".to_string(),
                tenant: None,
            }],
        }
    }
}

impl Default for AgentCard {
    fn default() -> Self {
        Self::new("http://localhost:9456")
    }
}

/// Agent card endpoint handler
///
/// Returns A2A v1.0 compliant agent card.
/// Endpoint: GET /.well-known/agent-card.json
///
/// URL priority: PUBLIC_URL env → X-Forwarded-Host header → localhost:port
async fn agent_card_handler(
    axum::extract::State(state): axum::extract::State<AppState>,
    headers: axum::http::HeaderMap,
) -> impl IntoResponse {
    let base_url = state.resolve_base_url(&headers);
    let card = AgentCard::new(&base_url);
    Json(card)
}

/// Agent card routes
pub fn routes() -> Router<AppState> {
    Router::new().route("/.well-known/agent-card.json", get(agent_card_handler))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_card_has_all_required_fields() {
        let card = AgentCard::new("http://localhost:9456");

        assert_eq!(card.name, "AgentBeacon Scheduler");
        assert_eq!(card.version, "1.0.0");
        assert!(!card.description.is_empty());
        assert!(!card.default_input_modes.is_empty());
        assert!(!card.default_output_modes.is_empty());
        assert!(!card.skills.is_empty());
        assert!(!card.supported_interfaces.is_empty());
    }

    #[test]
    fn test_agent_card_supported_interfaces() {
        let card = AgentCard::new("http://localhost:9456");

        let iface = &card.supported_interfaces[0];
        assert_eq!(iface.url, "http://localhost:9456/rpc");
        assert_eq!(iface.protocol_binding, "JSONRPC");
        assert_eq!(iface.protocol_version, "1.0");
        assert!(iface.tenant.is_none());
    }

    #[test]
    fn test_agent_card_serializes_to_json() {
        let card = AgentCard::new("http://localhost:9456");
        let json = serde_json::to_value(&card).expect("Failed to serialize");

        assert!(json.get("name").is_some());
        assert!(json.get("supportedInterfaces").is_some());
        assert!(json.get("defaultInputModes").is_some());
        assert!(json.get("defaultOutputModes").is_some());
        // v0.3 fields should NOT be present
        assert!(json.get("protocolVersion").is_none());
        assert!(json.get("url").is_none());
        assert!(json.get("preferredTransport").is_none());
        assert!(json.get("additionalInterfaces").is_none());
    }

    #[test]
    fn test_agent_card_matches_contract_structure() {
        let card = AgentCard::new("http://localhost:9456");
        let json = serde_json::to_value(&card).expect("Failed to serialize");

        let capabilities = json.get("capabilities").unwrap();
        assert!(capabilities.get("streaming").is_some());
        assert!(capabilities.get("pushNotifications").is_some());
        // v0.3 fields should NOT be present
        assert!(capabilities.get("methods").is_none());
        assert!(capabilities.get("features").is_none());

        let skills = json.get("skills").unwrap().as_array().unwrap();
        assert!(!skills.is_empty());
        let skill = &skills[0];
        assert!(skill.get("id").is_some());
        assert!(skill.get("name").is_some());
        assert!(skill.get("description").is_some());
        assert!(skill.get("inputModes").is_some());
        assert!(skill.get("outputModes").is_some());
    }

    #[test]
    fn test_agent_card_uses_dynamic_base_url() {
        let card1 = AgentCard::new("http://localhost:9456");
        assert_eq!(
            card1.supported_interfaces[0].url,
            "http://localhost:9456/rpc"
        );

        let card2 = AgentCard::new("http://localhost:19456");
        assert_eq!(
            card2.supported_interfaces[0].url,
            "http://localhost:19456/rpc"
        );

        let card3 = AgentCard::new("https://example.com:8080");
        assert_eq!(
            card3.supported_interfaces[0].url,
            "https://example.com:8080/rpc"
        );

        // Test with trailing slash
        let card4 = AgentCard::new("http://localhost:9456/");
        assert_eq!(
            card4.supported_interfaces[0].url,
            "http://localhost:9456/rpc"
        );
    }
}
