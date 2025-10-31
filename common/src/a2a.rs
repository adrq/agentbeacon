use serde::{Deserialize, Deserializer, Serialize};

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

/// A2A Protocol-compliant task status structure per A2A spec §6.2
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct A2ATaskStatus {
    /// Current state of the task's lifecycle
    pub state: String,
    /// Optional human-readable message providing status details
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<Message>,
    /// ISO 8601 datetime when status was recorded (validated on deserialization)
    #[serde(
        skip_serializing_if = "Option::is_none",
        deserialize_with = "validate_timestamp"
    )]
    pub timestamp: Option<String>,
}

/// A2A Protocol-compliant artifact structure for rich outputs per A2A spec §6.6
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct A2AArtifact {
    /// Unique identifier for the artifact
    pub artifact_id: String,
    /// Human-readable name
    pub name: String,
    /// Optional description
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// Content parts comprising the artifact
    pub parts: Vec<Part>,
}

/// A2A Protocol message structure per A2A spec §6.4
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Message {
    /// Unique identifier for the message
    pub message_id: String,
    /// Type discriminator (always "message")
    pub kind: String,
    /// Sender role: "user" or "agent"
    pub role: String,
    /// Message content parts
    pub parts: Vec<Part>,
}

/// A2A Protocol part union type per A2A spec §6.5
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(tag = "kind")]
pub enum Part {
    #[serde(rename = "text")]
    Text { text: String },

    #[serde(rename = "file")]
    File {
        /// File URI or base64-encoded bytes
        data: String,
        /// MIME type
        #[serde(rename = "mimeType")]
        mime_type: String,
    },

    #[serde(rename = "data")]
    Data {
        /// Structured data as JSON
        data: serde_json::Value,
    },
}

impl A2ATaskStatus {
    /// Create a completed task status
    pub fn completed() -> Self {
        Self {
            state: "completed".to_string(),
            message: None,
            timestamp: Some(chrono::Utc::now().to_rfc3339()),
        }
    }

    /// Create a failed task status with error message
    pub fn failed(error_text: String) -> Self {
        Self {
            state: "failed".to_string(),
            message: Some(Message {
                message_id: uuid::Uuid::new_v4().to_string(),
                kind: "message".to_string(),
                role: "agent".to_string(),
                parts: vec![Part::Text { text: error_text }],
            }),
            timestamp: Some(chrono::Utc::now().to_rfc3339()),
        }
    }

    /// Create a completed status with output message
    pub fn completed_with_output(output_text: String) -> Self {
        Self {
            state: "completed".to_string(),
            message: Some(Message {
                message_id: uuid::Uuid::new_v4().to_string(),
                kind: "message".to_string(),
                role: "agent".to_string(),
                parts: vec![Part::Text { text: output_text }],
            }),
            timestamp: Some(chrono::Utc::now().to_rfc3339()),
        }
    }

    /// Create a canceled task status with optional reason
    pub fn canceled(reason: String) -> Self {
        Self {
            state: "canceled".to_string(),
            message: Some(Message {
                message_id: uuid::Uuid::new_v4().to_string(),
                kind: "message".to_string(),
                role: "agent".to_string(),
                parts: vec![Part::Text { text: reason }],
            }),
            timestamp: Some(chrono::Utc::now().to_rfc3339()),
        }
    }
}
