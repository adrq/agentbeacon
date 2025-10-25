use axum::{Json, Router, response::IntoResponse, routing::get};
use serde::{Deserialize, Serialize};

use crate::app::AppState;

/// A2A Protocol v0.3.0 Capabilities
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Capabilities {
    pub streaming: bool,
    #[serde(rename = "pushNotifications")]
    pub push_notifications: bool,
    pub methods: Vec<String>,
    pub features: Vec<String>,
}

/// A2A Protocol v0.3.0 Skill
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    pub id: String,
    pub name: String,
    pub description: String,
    #[serde(rename = "inputModes")]
    pub input_modes: Vec<String>,
    #[serde(rename = "outputModes")]
    pub output_modes: Vec<String>,
}

/// A2A Protocol v0.3.0 Additional Interface
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AdditionalInterface {
    pub url: String,
    pub transport: String,
}

/// A2A Protocol v0.3.0 Agent Card (Entity 6)
///
/// All 10 required fields per A2A v0.3.0 specification:
/// - name, version, protocolVersion, url, description
/// - preferredTransport, defaultInputModes, defaultOutputModes
/// - capabilities, skills
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentCard {
    pub name: String,
    pub version: String,
    #[serde(rename = "protocolVersion")]
    pub protocol_version: String,
    pub url: String,
    pub description: String,
    #[serde(rename = "preferredTransport")]
    pub preferred_transport: String,
    #[serde(rename = "defaultInputModes")]
    pub default_input_modes: Vec<String>,
    #[serde(rename = "defaultOutputModes")]
    pub default_output_modes: Vec<String>,
    pub capabilities: Capabilities,
    pub skills: Vec<Skill>,
    #[serde(
        rename = "additionalInterfaces",
        skip_serializing_if = "Option::is_none"
    )]
    pub additional_interfaces: Option<Vec<AdditionalInterface>>,
}

impl AgentCard {
    /// Create A2A v0.3.0 compliant agent card for scheduler
    pub fn new(base_url: &str) -> Self {
        let rpc_url = format!("{}/rpc", base_url.trim_end_matches('/'));
        Self {
            name: "AgentMaestro Scheduler".to_string(),
            version: "1.0.0".to_string(),
            protocol_version: "0.3.0".to_string(),
            url: rpc_url.clone(),
            description: "AgentMaestro scheduler for AI agent workflow orchestration with DAG-based task scheduling and workflow registry support".to_string(),
            preferred_transport: "JSONRPC".to_string(),
            default_input_modes: vec![
                "application/x-yaml".to_string(),
                "text/plain".to_string(),
            ],
            default_output_modes: vec!["application/json".to_string()],
            capabilities: Capabilities {
                streaming: false,
                push_notifications: false,
                methods: vec!["message/send".to_string(), "tasks/get".to_string()],
                features: vec![
                    "workflow-orchestration".to_string(),
                    "dag-scheduling".to_string(),
                    "workflow-registry".to_string(),
                    "namespace-support".to_string(),
                    "fifo-task-assignment".to_string(),
                ],
            },
            skills: vec![Skill {
                id: "workflow-orchestration".to_string(),
                name: "Workflow Orchestration".to_string(),
                description: "Submit and execute multi-agent AI workflows via DAG scheduling. Supports both inline YAML and registry-based workflow references with versioning and namespace organization.".to_string(),
                input_modes: vec![
                    "application/x-yaml".to_string(),
                    "text/plain".to_string(),
                ],
                output_modes: vec!["application/json".to_string()],
            }],
            additional_interfaces: Some(vec![AdditionalInterface {
                url: rpc_url,
                transport: "JSONRPC".to_string(),
            }]),
        }
    }
}

impl Default for AgentCard {
    fn default() -> Self {
        Self::new("http://localhost:9456")
    }
}

/// Agent card endpoint handler (FR-001)
///
/// Returns A2A v0.3.0 compliant agent card with all required fields.
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

        // Verify all 10 A2A v0.3.0 required fields
        assert_eq!(card.name, "AgentMaestro Scheduler");
        assert_eq!(card.version, "1.0.0");
        assert_eq!(card.protocol_version, "0.3.0");
        assert_eq!(card.url, "http://localhost:9456/rpc");
        assert!(!card.description.is_empty());
        assert_eq!(card.preferred_transport, "JSONRPC");
        assert!(!card.default_input_modes.is_empty());
        assert!(!card.default_output_modes.is_empty());
        assert!(!card.capabilities.methods.is_empty());
        assert!(!card.skills.is_empty());
    }

    #[test]
    fn test_agent_card_declares_required_methods() {
        let card = AgentCard::new("http://localhost:9456");

        assert!(
            card.capabilities
                .methods
                .contains(&"message/send".to_string())
        );
        assert!(card.capabilities.methods.contains(&"tasks/get".to_string()));
    }

    #[test]
    fn test_agent_card_serializes_to_json() {
        let card = AgentCard::new("http://localhost:9456");
        let json = serde_json::to_value(&card).expect("Failed to serialize");

        // Check camelCase field names
        assert!(json.get("name").is_some());
        assert!(json.get("protocolVersion").is_some());
        assert!(json.get("preferredTransport").is_some());
        assert!(json.get("defaultInputModes").is_some());
        assert!(json.get("defaultOutputModes").is_some());
    }

    #[test]
    fn test_agent_card_matches_contract_structure() {
        let card = AgentCard::new("http://localhost:9456");
        let json = serde_json::to_value(&card).expect("Failed to serialize");

        // Verify structure matches contract specification
        assert!(json.get("capabilities").is_some());
        assert!(json.get("skills").is_some());

        let capabilities = json.get("capabilities").unwrap();
        assert!(capabilities.get("streaming").is_some());
        assert!(capabilities.get("pushNotifications").is_some());
        assert!(capabilities.get("methods").is_some());
        assert!(capabilities.get("features").is_some());

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
        // Test with different base URLs
        let card1 = AgentCard::new("http://localhost:9456");
        assert_eq!(card1.url, "http://localhost:9456/rpc");
        assert_eq!(
            card1.additional_interfaces.as_ref().unwrap()[0].url,
            "http://localhost:9456/rpc"
        );

        let card2 = AgentCard::new("http://localhost:19456");
        assert_eq!(card2.url, "http://localhost:19456/rpc");
        assert_eq!(
            card2.additional_interfaces.as_ref().unwrap()[0].url,
            "http://localhost:19456/rpc"
        );

        let card3 = AgentCard::new("https://example.com:8080");
        assert_eq!(card3.url, "https://example.com:8080/rpc");
        assert_eq!(
            card3.additional_interfaces.as_ref().unwrap()[0].url,
            "https://example.com:8080/rpc"
        );

        // Test with trailing slash
        let card4 = AgentCard::new("http://localhost:9456/");
        assert_eq!(card4.url, "http://localhost:9456/rpc");
        assert_eq!(
            card4.additional_interfaces.as_ref().unwrap()[0].url,
            "http://localhost:9456/rpc"
        );
    }
}
