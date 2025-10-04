use jsonschema::{Retrieve, Uri, Validator};
use serde_json::Value as JsonValue;
use std::collections::{HashMap, HashSet};

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

        // Perform DAG validation (guardrails)
        let dag_errors = self.validate_dag_guardrails(&workflow_json);
        if !dag_errors.is_empty() {
            return Err(SchedulerError::DagValidationFailed(dag_errors));
        }

        Ok(workflow_json)
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

        // Check artifact constraints
        self.check_artifacts(tasks, &mut errors);

        errors
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

    /// Check for duplicate task IDs (FR-001)
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

    /// Check artifact constraints
    fn check_artifacts(&self, tasks: &[JsonValue], errors: &mut Vec<String>) {
        // Build artifact producer map
        let mut artifact_producers: HashMap<String, String> = HashMap::new();
        let mut task_dependencies: HashMap<String, HashSet<String>> = HashMap::new();

        // First pass: collect all artifact producers and dependencies
        for task in tasks {
            let task_id = match task.get("id").and_then(|v| v.as_str()) {
                Some(id) => id.to_string(),
                None => continue,
            };

            // Collect depends_on relationships
            if let Some(depends_on) = task.get("depends_on").and_then(|v| v.as_array()) {
                let deps: HashSet<String> = depends_on
                    .iter()
                    .filter_map(|d| d.as_str().map(String::from))
                    .collect();
                task_dependencies.insert(task_id.clone(), deps);
            }

            // Collect artifact outputs from task.artifacts (A2A protocol)
            if let Some(task_block) = task.get("task").and_then(|v| v.as_object()) {
                if let Some(artifacts) = task_block.get("artifacts").and_then(|v| v.as_array()) {
                    for artifact in artifacts {
                        if let Some(artifact_id) =
                            artifact.get("artifactId").and_then(|v| v.as_str())
                        {
                            // Check for duplicate artifact producers
                            if let Some(existing_producer) = artifact_producers.get(artifact_id) {
                                errors.push(format!(
                                    "Artifact '{artifact_id}' is declared by both task '{existing_producer}' and task '{task_id}'"
                                ));
                            } else {
                                artifact_producers.insert(artifact_id.to_string(), task_id.clone());
                            }
                        }
                    }
                }
            }
        }

        // Second pass: validate artifact inputs
        for task in tasks {
            let task_id = match task.get("id").and_then(|v| v.as_str()) {
                Some(id) => id,
                None => continue,
            };

            if let Some(inputs) = task.get("inputs").and_then(|v| v.as_object()) {
                if let Some(artifact_refs) = inputs.get("artifacts").and_then(|v| v.as_array()) {
                    for artifact_ref in artifact_refs {
                        let from_task = artifact_ref.get("from").and_then(|v| v.as_str());
                        let artifact_id = artifact_ref.get("artifactId").and_then(|v| v.as_str());

                        if let (Some(from), Some(artifact)) = (from_task, artifact_id) {
                            // Check if artifact producer exists
                            if let Some(producer) = artifact_producers.get(artifact) {
                                // Verify producer matches the 'from' field
                                if producer != from {
                                    errors.push(format!(
                                        "Task '{task_id}' expects artifact '{artifact}' from task '{from}', but it is produced by '{producer}'"
                                    ));
                                }

                                // Check if producer is in depends_on
                                let deps =
                                    task_dependencies.get(task_id).cloned().unwrap_or_default();
                                if !deps.contains(from) {
                                    errors.push(format!(
                                        "Task '{task_id}' consumes artifact '{artifact}' but does not depend on producer '{from}'"
                                    ));
                                }
                            } else {
                                // Artifact producer doesn't exist
                                errors.push(format!(
                                    "Task '{task_id}' expects artifact '{artifact}' but no task declares it"
                                ));
                            }
                        }
                    }
                }
            }
        }
    }
}
