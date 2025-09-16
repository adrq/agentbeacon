use jsonschema::{Retrieve, Uri, Validator};
use serde_json::Value as JsonValue;
use std::collections::HashMap;

use crate::error::SchedulerError;

/// InMemoryRetriever for cross-schema $ref resolution (T019 requirement)
///
/// Enables workflow-schema.json to reference a2a-v0.3.0.schema.json definitions
/// without requiring filesystem access during validation.
struct InMemoryRetriever {
    schemas: HashMap<String, JsonValue>,
}

impl InMemoryRetriever {
    fn new() -> Self {
        let mut schemas = HashMap::new();

        // Load A2A schema for cross-schema $ref resolution
        let a2a_schema_str = include_str!("../../../docs/a2a-v0.3.0.schema.json");
        if let Ok(a2a_schema) = serde_json::from_str(a2a_schema_str) {
            schemas.insert("a2a-v0.3.0.schema.json".to_string(), a2a_schema);
        }

        InMemoryRetriever { schemas }
    }
}

impl Retrieve for InMemoryRetriever {
    fn retrieve(
        &self,
        uri: &Uri<String>,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error + Send + Sync>> {
        // Extract filename from URI - handle both relative and absolute URLs
        let uri_str = uri.as_str();
        let filename = uri_str
            .trim_start_matches("file://")
            .split('/')
            .next_back()
            .unwrap_or(uri_str)
            .split('#')
            .next()
            .unwrap_or(uri_str);

        self.schemas
            .get(filename)
            .cloned()
            .ok_or_else(|| format!("Schema not found: {filename} (from URI: {uri_str})").into())
    }
}

/// Schema validator with compile-once semantics
pub struct SchemaValidator {
    compiled: Validator,
}

impl SchemaValidator {
    /// Create new validator, loading all schemas at startup (fail-fast on errors)
    pub fn new() -> Result<Self, SchedulerError> {
        // Load and parse workflow schema
        let workflow_schema_str = include_str!("../../../docs/workflow-schema.json");
        let workflow_schema: JsonValue =
            serde_json::from_str(workflow_schema_str).map_err(|e| {
                SchedulerError::SchemaCompilation(format!("Failed to parse workflow schema: {e}"))
            })?;

        // Validate that schema is well-formed
        if !workflow_schema.is_object() {
            return Err(SchedulerError::SchemaCompilation(
                "workflow-schema.json is not a valid JSON object".to_string(),
            ));
        }

        // Create custom retriever for cross-schema $ref resolution
        let retriever = InMemoryRetriever::new();

        // Compile schema ONCE at startup with custom retriever (T019 requirement)
        let compiled = jsonschema::options()
            .with_retriever(retriever)
            .build(&workflow_schema)
            .map_err(|e| {
                SchedulerError::SchemaCompilation(format!("Failed to compile workflow schema: {e}"))
            })?;

        Ok(SchemaValidator { compiled })
    }

    /// Validate workflow YAML against workflow-schema.json
    pub fn validate_workflow_yaml(&self, yaml_content: &str) -> Result<JsonValue, SchedulerError> {
        // Parse YAML to JSON
        let workflow_json: JsonValue = serde_yaml::from_str(yaml_content)
            .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid YAML syntax: {e}")))?;

        // Validate using pre-compiled schema (no recompilation on each call)
        // Use iter_errors() to get all validation errors for detailed feedback
        let validation_errors: Vec<String> = self
            .compiled
            .iter_errors(&workflow_json)
            .map(|e| format!("  - {e}"))
            .collect();

        if !validation_errors.is_empty() {
            return Err(SchedulerError::ValidationFailed(format!(
                "Schema validation failed:\n{}",
                validation_errors.join("\n")
            )));
        }

        Ok(workflow_json)
    }

    /// Validate task payload against A2A v0.3.0 schema (FR-010)
    pub fn validate_task(&self, task: &JsonValue) -> Result<(), SchedulerError> {
        // For now, skip strict schema validation in tests
        // TODO: Implement proper A2A Task schema validation with cross-reference resolution
        // The challenge is that Task schema has $ref to other definitions in the A2A schema
        // and we need to provide the full schema context for validation to work.

        // Basic validation: ensure task has required fields
        if !task.is_object() {
            return Err(SchedulerError::ValidationFailed(
                "Task must be a JSON object".to_string(),
            ));
        }

        let obj = task.as_object().unwrap();

        // Check for required field: history
        if !obj.contains_key("history") {
            return Err(SchedulerError::ValidationFailed(
                "Task must have 'history' field".to_string(),
            ));
        }

        Ok(())
    }
}
