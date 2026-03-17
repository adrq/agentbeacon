use serde::{Deserialize, Deserializer, Serialize};

/// A2A v1.0 task state enum values (wire format).
///
/// Convention: status values use SCREAMING_SNAKE_CASE at the A2A protocol boundary.
/// Internal DB/Rust code uses lowercase short form ("submitted", "completed", etc.).
/// Translation happens at the A2A response serialization boundary only.
pub mod task_state {
    pub const UNSPECIFIED: &str = "TASK_STATE_UNSPECIFIED";
    pub const SUBMITTED: &str = "TASK_STATE_SUBMITTED";
    pub const WORKING: &str = "TASK_STATE_WORKING";
    pub const COMPLETED: &str = "TASK_STATE_COMPLETED";
    pub const FAILED: &str = "TASK_STATE_FAILED";
    pub const CANCELED: &str = "TASK_STATE_CANCELED";
    pub const REJECTED: &str = "TASK_STATE_REJECTED";
    pub const INPUT_REQUIRED: &str = "TASK_STATE_INPUT_REQUIRED";
    pub const AUTH_REQUIRED: &str = "TASK_STATE_AUTH_REQUIRED";
}

/// A2A v1.0 role enum values.
///
/// Convention: role values use A2A wire format (ROLE_USER, ROLE_AGENT) everywhere,
/// including stored event payloads. Unlike status values, roles have no internal
/// short form — the A2A format is canonical.
pub mod role {
    pub const UNSPECIFIED: &str = "ROLE_UNSPECIFIED";
    pub const USER: &str = "ROLE_USER";
    pub const AGENT: &str = "ROLE_AGENT";
}

/// Validate timestamp field is valid RFC3339 format per A2A spec §6.2
fn validate_timestamp<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: Deserializer<'de>,
{
    let s: Option<String> = Option::deserialize(deserializer)?;
    if let Some(timestamp) = &s {
        chrono::DateTime::parse_from_rfc3339(timestamp)
            .map_err(|e| serde::de::Error::custom(format!("invalid timestamp: {e}")))?;
    }
    Ok(s)
}

/// A2A v1.0 Protocol-compliant task status structure
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct A2ATaskStatus {
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<Message>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "validate_timestamp"
    )]
    pub timestamp: Option<String>,
}

/// A2A v1.0 Protocol-compliant artifact structure
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct A2AArtifact {
    pub artifact_id: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub parts: Vec<Part>,
}

/// A2A v1.0 Protocol message structure
///
/// Removed from v0.3: `kind` field (was always "message").
/// Role values: "ROLE_USER" | "ROLE_AGENT" (v1.0 enum format).
#[derive(Debug, Default, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Message {
    pub message_id: String,
    pub role: String,
    pub parts: Vec<Part>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reference_task_ids: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extensions: Option<Vec<String>>,
}

/// A2A v1.0 Protocol part — unified struct with member-presence polymorphism.
///
/// Exactly one of text/url/raw/data should be set (A2A 1.0 protobuf oneof).
/// This struct does not enforce the invariant at the type level — use the
/// constructors (Part::text(), Part::data()) for internal construction.
#[derive(Debug, Default, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Part {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    /// Inline binary content (base64-encoded in JSON)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filename: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<serde_json::Value>,
}

impl Part {
    pub fn text(s: impl Into<String>) -> Self {
        Self {
            text: Some(s.into()),
            ..Default::default()
        }
    }

    pub fn data(v: serde_json::Value) -> Self {
        Self {
            data: Some(v),
            ..Default::default()
        }
    }
}

impl A2ATaskStatus {
    pub fn completed() -> Self {
        Self {
            state: task_state::COMPLETED.to_string(),
            message: None,
            timestamp: Some(
                chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            ),
        }
    }

    pub fn failed(error_text: String) -> Self {
        Self {
            state: task_state::FAILED.to_string(),
            message: Some(Message {
                message_id: uuid::Uuid::new_v4().to_string(),
                role: role::AGENT.to_string(),
                parts: vec![Part::text(error_text)],
                ..Default::default()
            }),
            timestamp: Some(
                chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            ),
        }
    }

    pub fn completed_with_output(output_text: String) -> Self {
        Self {
            state: task_state::COMPLETED.to_string(),
            message: Some(Message {
                message_id: uuid::Uuid::new_v4().to_string(),
                role: role::AGENT.to_string(),
                parts: vec![Part::text(output_text)],
                ..Default::default()
            }),
            timestamp: Some(
                chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            ),
        }
    }

    pub fn canceled(reason: String) -> Self {
        Self {
            state: task_state::CANCELED.to_string(),
            message: Some(Message {
                message_id: uuid::Uuid::new_v4().to_string(),
                role: role::AGENT.to_string(),
                parts: vec![Part::text(reason)],
                ..Default::default()
            }),
            timestamp: Some(
                chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            ),
        }
    }
}
