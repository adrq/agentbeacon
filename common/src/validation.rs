use crate::schemas::{A2A_SCHEMA, AGENTS_SCHEMA, SYNC_REQUEST_SCHEMA, SYNC_RESPONSE_SCHEMA};
use jsonschema::{Draft, Retrieve, Validator};
use referencing::Uri;
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub enum ValidationError {
    SchemaViolation(Vec<String>),
    InvalidJson(String),
}

impl std::fmt::Display for ValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ValidationError::SchemaViolation(errors) => {
                write!(f, "Schema validation failed: {}", errors.join(", "))
            }
            ValidationError::InvalidJson(msg) => write!(f, "Invalid JSON: {msg}"),
        }
    }
}

impl std::error::Error for ValidationError {}

pub struct InMemoryRetriever {
    schemas: HashMap<String, Value>,
}

impl InMemoryRetriever {
    pub fn new() -> Result<Self, Box<dyn std::error::Error>> {
        let mut schemas = HashMap::new();

        schemas.insert(
            "agents-schema.json".to_string(),
            serde_json::from_str(AGENTS_SCHEMA)?,
        );
        schemas.insert(
            "a2a-v0.3.0.schema.json".to_string(),
            serde_json::from_str(A2A_SCHEMA)?,
        );
        schemas.insert(
            "worker-sync-request.schema.json".to_string(),
            serde_json::from_str(SYNC_REQUEST_SCHEMA)?,
        );
        schemas.insert(
            "worker-sync-response.schema.json".to_string(),
            serde_json::from_str(SYNC_RESPONSE_SCHEMA)?,
        );

        Ok(Self { schemas })
    }
}

impl Retrieve for InMemoryRetriever {
    fn retrieve(
        &self,
        uri: &Uri<String>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let uri_str = uri.as_str();
        let filename = uri_str.split('/').next_back().unwrap_or(uri_str);
        self.schemas
            .get(filename)
            .cloned()
            .ok_or_else(|| format!("Schema not found: {uri_str}").into())
    }
}

fn build_validator(
    schema_str: &str,
    name: &str,
) -> Result<Validator, Box<dyn std::error::Error + Send + Sync>> {
    let schema: Value = serde_json::from_str(schema_str)
        .map_err(|e| format!("Failed to parse {name} schema: {e}"))?;
    let retriever =
        InMemoryRetriever::new().map_err(|e| format!("Failed to create schema retriever: {e}"))?;
    Validator::options()
        .with_draft(Draft::Draft7)
        .with_retriever(retriever)
        .build(&schema)
        .map_err(|e| format!("Failed to compile {name} schema: {e}").into())
}

lazy_static::lazy_static! {
    static ref AGENTS_VALIDATOR: Result<Validator, Box<dyn std::error::Error + Send + Sync>> = {
        build_validator(AGENTS_SCHEMA, "agents")
    };

    static ref SYNC_REQUEST_VALIDATOR: Result<Validator, Box<dyn std::error::Error + Send + Sync>> = {
        build_validator(SYNC_REQUEST_SCHEMA, "sync request")
    };

    static ref SYNC_RESPONSE_VALIDATOR: Result<Validator, Box<dyn std::error::Error + Send + Sync>> = {
        build_validator(SYNC_RESPONSE_SCHEMA, "sync response")
    };

    static ref A2A_VALIDATOR: Result<Validator, Box<dyn std::error::Error + Send + Sync>> = {
        build_validator(A2A_SCHEMA, "A2A")
    };
}

pub fn validate_agents_config(config: &Value) -> Result<(), ValidationError> {
    let validator = AGENTS_VALIDATOR
        .as_ref()
        .map_err(|e| ValidationError::InvalidJson(format!("Schema compilation failed: {e}")))?;

    match validator.validate(config) {
        Ok(_) => Ok(()),
        Err(e) => {
            let error_message = format!("{}: {}", e.instance_path, e);
            Err(ValidationError::SchemaViolation(vec![error_message]))
        }
    }
}

pub fn validate_sync_request(request: &Value) -> Result<(), ValidationError> {
    let validator = SYNC_REQUEST_VALIDATOR
        .as_ref()
        .map_err(|e| ValidationError::InvalidJson(format!("Schema compilation failed: {e}")))?;

    match validator.validate(request) {
        Ok(_) => Ok(()),
        Err(e) => {
            let error_message = format!("{}: {}", e.instance_path, e);
            Err(ValidationError::SchemaViolation(vec![error_message]))
        }
    }
}

pub fn validate_sync_response(response: &Value) -> Result<(), ValidationError> {
    let validator = SYNC_RESPONSE_VALIDATOR
        .as_ref()
        .map_err(|e| ValidationError::InvalidJson(format!("Schema compilation failed: {e}")))?;

    match validator.validate(response) {
        Ok(_) => Ok(()),
        Err(e) => {
            let error_message = format!("{}: {}", e.instance_path, e);
            Err(ValidationError::SchemaViolation(vec![error_message]))
        }
    }
}

pub fn validate_a2a_request(request: &Value) -> Result<(), ValidationError> {
    let validator = A2A_VALIDATOR
        .as_ref()
        .map_err(|e| ValidationError::InvalidJson(format!("Schema compilation failed: {e}")))?;

    // Validate against A2A JSON-RPC request schema
    // The A2A schema contains definitions for all request types (SendMessageRequest, etc.)
    match validator.validate(request) {
        Ok(_) => Ok(()),
        Err(e) => {
            let error_message = format!("{}: {}", e.instance_path, e);
            Err(ValidationError::SchemaViolation(vec![error_message]))
        }
    }
}
