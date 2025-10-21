use jsonschema::{Retrieve, Uri, Validator};
use serde_json::Value as JsonValue;
use std::collections::{HashMap, HashSet};

use crate::error::SchedulerError;

/// InMemoryRetriever for cross-schema $ref resolution
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

        // Compile schema ONCE at startup with custom retriever
        let compiled = jsonschema::options()
            .with_retriever(retriever)
            .build(&workflow_schema)
            .map_err(|e| {
                SchedulerError::SchemaCompilation(format!("Failed to compile workflow schema: {e}"))
            })?;

        Ok(SchemaValidator { compiled })
    }

    /// Validate workflow YAML against schema with runtime field injection support
    ///
    /// Two-phase validation approach:
    /// 1. Parse YAML → JSON and inject temporary messageId/kind values for schema validation
    /// 2. Validate injected workflow against schema (catches structure errors)
    /// 3. Run DAG guardrails (duplicate IDs, cycles, missing dependencies)
    ///
    /// Returns ORIGINAL workflow JSON without injected fields - runtime injection
    /// happens later during task assignment via task_preparation module.
    pub fn validate_workflow_yaml(&self, yaml_content: &str) -> Result<JsonValue, SchedulerError> {
        // Parse YAML to JSON
        let workflow_json: JsonValue = serde_yaml::from_str(yaml_content)
            .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid YAML syntax: {e}")))?;

        // Clone workflow for validation injection
        let mut workflow_for_validation = workflow_json.clone();

        // Inject temporary messageId/kind values for validation
        Self::inject_validation_fields(&mut workflow_for_validation);

        // Validate using pre-compiled schema (no recompilation on each call)
        // Use iter_errors() to get all validation errors for detailed feedback
        let validation_errors: Vec<String> = self
            .compiled
            .iter_errors(&workflow_for_validation)
            .map(|e| format!("  - {e}"))
            .collect();

        if !validation_errors.is_empty() {
            return Err(SchedulerError::ValidationFailed(format!(
                "Schema validation failed:\n{}",
                validation_errors.join("\n")
            )));
        }

        // Perform DAG validation (guardrails) on original data
        let dag_errors = self.validate_dag_guardrails(&workflow_json);
        if !dag_errors.is_empty() {
            return Err(SchedulerError::DagValidationFailed(dag_errors));
        }

        // Return ORIGINAL unmodified workflow
        Ok(workflow_json)
    }

    /// Inject temporary messageId/kind values for validation
    /// Mutates the workflow JSON to add missing fields
    fn inject_validation_fields(workflow: &mut JsonValue) {
        if let Some(tasks) = workflow.get_mut("tasks").and_then(|t| t.as_array_mut()) {
            for task in tasks {
                if let Some(task_obj) = task.get_mut("task").and_then(|t| t.as_object_mut()) {
                    // Handle MessageSendParams structure (task.message)
                    if let Some(message) =
                        task_obj.get_mut("message").and_then(|m| m.as_object_mut())
                    {
                        // Inject messageId if absent
                        if !message.contains_key("messageId") {
                            message.insert(
                                "messageId".to_string(),
                                JsonValue::String("temp".to_string()),
                            );
                        }
                        // Inject kind if absent
                        if !message.contains_key("kind") {
                            message.insert(
                                "kind".to_string(),
                                JsonValue::String("message".to_string()),
                            );
                        }
                    }
                }
            }
        }
    }

    /// Validate DAG guardrails (Phase 1 implementation)
    /// Returns a vector of human-readable error messages
    fn validate_dag_guardrails(&self, workflow: &JsonValue) -> Vec<String> {
        let mut errors = Vec::new();

        // Extract tasks array
        let tasks = match workflow.get("tasks").and_then(|t| t.as_array()) {
            Some(tasks) => tasks,
            None => return errors, // Schema validation should have caught this
        };

        // Check for duplicate task IDs
        self.check_duplicate_task_ids(tasks, &mut errors);

        // Check dependencies (existence, cycles, self-references)
        self.check_dependencies(tasks, &mut errors);

        errors
    }

    /// Lightweight sanity check for task structure after runtime field injection.
    ///
    /// Note: Full schema validation already occurred in validate_workflow() before
    /// runtime field injection. This method provides a minimal safety check to catch
    /// potential bugs in the injection logic, but should not duplicate schema validation.
    ///
    /// If this check fails, it indicates a bug in task_preparation module, not a
    /// workflow authoring error.
    pub fn validate_task(&self, task: &JsonValue) -> Result<(), SchedulerError> {
        // Sanity check: task must be an object
        if !task.is_object() {
            return Err(SchedulerError::ValidationFailed(
                "Internal error: Task is not a JSON object after injection".to_string(),
            ));
        }

        // Note: No need to check for "message" field here - schema validation already
        // ensured it exists, and injection doesn't modify the structure.
        Ok(())
    }

    /// Check for duplicate task IDs
    fn check_duplicate_task_ids(&self, tasks: &[JsonValue], errors: &mut Vec<String>) {
        let mut seen_ids = HashSet::new();

        for task in tasks {
            if let Some(id) = task.get("id").and_then(|v| v.as_str()) {
                if !seen_ids.insert(id) {
                    errors.push(format!("Task id '{id}' is declared more than once"));
                }
            }
        }
    }

    /// Check dependencies (existence, cycles, self-references)
    fn check_dependencies(&self, tasks: &[JsonValue], errors: &mut Vec<String>) {
        // Build task ID set and dependency map
        let mut task_ids = HashSet::new();
        let mut dependencies: HashMap<&str, Vec<&str>> = HashMap::new();

        for task in tasks {
            if let Some(id) = task.get("id").and_then(|v| v.as_str()) {
                task_ids.insert(id);

                if let Some(depends_on) = task.get("depends_on").and_then(|v| v.as_array()) {
                    let deps: Vec<&str> = depends_on.iter().filter_map(|d| d.as_str()).collect();
                    dependencies.insert(id, deps);
                }
            }
        }

        // Check dependency existence and self-references
        for (task_id, deps) in &dependencies {
            for dep in deps {
                // Check for self-dependency
                if dep == task_id {
                    errors.push(format!("Task '{task_id}' cannot depend on itself"));
                }

                // Check if dependency exists
                if !task_ids.contains(dep) {
                    errors.push(format!(
                        "Task '{task_id}' depends on '{dep}' which does not exist"
                    ));
                }
            }
        }

        // Check for cycles using DFS
        let mut visited = HashSet::new();
        let mut rec_stack = HashSet::new();

        for task_id in &task_ids {
            if !visited.contains(task_id)
                && Self::has_cycle(task_id, &dependencies, &mut visited, &mut rec_stack)
            {
                errors.push("Workflow contains circular dependencies".to_string());
                break; // Report cycle once
            }
        }
    }

    /// Helper for cycle detection using DFS
    fn has_cycle<'a>(
        node: &'a str,
        dependencies: &HashMap<&'a str, Vec<&'a str>>,
        visited: &mut HashSet<&'a str>,
        rec_stack: &mut HashSet<&'a str>,
    ) -> bool {
        visited.insert(node);
        rec_stack.insert(node);

        if let Some(deps) = dependencies.get(node) {
            for dep in deps {
                if !visited.contains(dep) {
                    if Self::has_cycle(dep, dependencies, visited, rec_stack) {
                        return true;
                    }
                } else if rec_stack.contains(dep) {
                    return true; // Back edge found - cycle detected
                }
            }
        }

        rec_stack.remove(node);
        false
    }
}
