use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

/// WorkflowDAG represents a parsed workflow dependency graph for scheduling logic
#[derive(Debug, Clone)]
pub struct WorkflowDAG {
    /// Map of task ID to task details
    pub tasks: HashMap<String, Task>,
    /// Map of task ID to list of dependency task IDs (tasks this task depends on)
    pub dependencies: HashMap<String, Vec<String>>,
    /// Map of task ID to list of dependent task IDs (tasks that depend on this task)
    pub dependents: HashMap<String, Vec<String>>,
}

/// Retry backoff strategy
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum BackoffStrategy {
    Fixed,
    Linear,
    Exponential,
}

/// Retry policy from workflow schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryPolicy {
    #[serde(default = "default_attempts")]
    pub attempts: u32,
    #[serde(default = "default_backoff")]
    pub backoff: BackoffStrategy,
    #[serde(default)]
    pub delay_seconds: u64,
}

fn default_attempts() -> u32 {
    1
}

fn default_backoff() -> BackoffStrategy {
    BackoffStrategy::Fixed
}

/// Execution policy from workflow schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionPolicy {
    pub timeout: Option<u64>,
    pub retry: Option<RetryPolicy>,
}

/// Task definition from workflow YAML
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub agent: String,
    pub task: serde_json::Value,
    #[serde(default)]
    pub depends_on: Vec<String>,
    pub execution: Option<ExecutionPolicy>,
}

/// Workflow structure parsed from YAML
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Workflow {
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    pub tasks: Vec<Task>,
}

#[derive(Debug, thiserror::Error)]
pub enum DagError {
    #[error("Empty DAG: workflow must contain at least one task")]
    EmptyDag,

    #[error("Circular dependency detected: {0}")]
    CyclicDependency(String),

    #[error("Invalid dependency: task '{task}' depends on non-existent task '{dependency}'")]
    NonexistentDependency { task: String, dependency: String },

    #[error("Failed to parse workflow: {0}")]
    ParseError(String),
}

impl WorkflowDAG {
    /// Build WorkflowDAG from workflow YAML
    pub fn from_workflow(yaml: &str) -> Result<Self, DagError> {
        // Parse YAML to Workflow struct
        let workflow: Workflow = serde_yaml::from_str(yaml)
            .map_err(|e| DagError::ParseError(format!("YAML parse error: {e}")))?;

        // Validate non-empty (FR-037)
        if workflow.tasks.is_empty() {
            return Err(DagError::EmptyDag);
        }

        let mut tasks = HashMap::new();
        let mut dependencies = HashMap::new();
        let mut dependents: HashMap<String, Vec<String>> = HashMap::new();

        // Build tasks map and initialize dependency structures
        for task in workflow.tasks {
            let task_id = task.id.clone(); // Clone ONCE

            // Initialize dependents list (before consuming task)
            if !dependents.contains_key(&task_id) {
                dependents.insert(task_id.clone(), Vec::new());
            }

            // Build reverse dependency map (before consuming task)
            for dep in &task.depends_on {
                dependents
                    .entry(dep.clone())
                    .or_default()
                    .push(task_id.clone());
            }

            // Store dependencies (clone Vec, not individual strings)
            dependencies.insert(task_id.clone(), task.depends_on.clone());

            // LAST: Move task into HashMap (no clone needed)
            tasks.insert(task_id, task);
        }

        let dag = WorkflowDAG {
            tasks,
            dependencies,
            dependents,
        };

        // Validate all dependencies exist
        dag.validate_dependencies()?;

        // Detect cycles
        dag.detect_cycles()?;

        Ok(dag)
    }

    /// Validate that all dependencies reference existing tasks
    fn validate_dependencies(&self) -> Result<(), DagError> {
        for (task_id, deps) in &self.dependencies {
            for dep in deps {
                if !self.tasks.contains_key(dep) {
                    return Err(DagError::NonexistentDependency {
                        task: task_id.clone(),
                        dependency: dep.clone(),
                    });
                }
            }
        }
        Ok(())
    }

    /// Detect cycles using DFS with recursion stack (FR-014)
    pub fn detect_cycles(&self) -> Result<(), DagError> {
        let mut visited = HashSet::new();
        let mut rec_stack = HashSet::new();

        for task_id in self.tasks.keys() {
            if !visited.contains(task_id) {
                self.dfs_cycle_check(task_id, &mut visited, &mut rec_stack)?;
            }
        }

        Ok(())
    }

    /// DFS helper for cycle detection
    fn dfs_cycle_check(
        &self,
        node: &str,
        visited: &mut HashSet<String>,
        rec_stack: &mut HashSet<String>,
    ) -> Result<(), DagError> {
        visited.insert(node.to_string());
        rec_stack.insert(node.to_string());

        if let Some(deps) = self.dependencies.get(node) {
            for dep in deps {
                if !visited.contains(dep) {
                    self.dfs_cycle_check(dep, visited, rec_stack)?;
                } else if rec_stack.contains(dep) {
                    return Err(DagError::CyclicDependency(format!(
                        "Cycle detected involving task '{node}' and '{dep}'"
                    )));
                }
            }
        }

        rec_stack.remove(node);
        Ok(())
    }

    /// Get entry nodes (tasks with no dependencies) - FR-015
    pub fn entry_nodes(&self) -> Vec<String> {
        self.dependencies
            .iter()
            .filter(|(_, deps)| deps.is_empty())
            .map(|(id, _)| id.clone())
            .collect()
    }

    /// Get ready nodes whose dependencies are all complete - FR-016
    pub fn ready_nodes(&self, completed: &HashSet<String>) -> Vec<String> {
        self.tasks
            .keys()
            .filter(|task_id| {
                // Skip already completed tasks
                if completed.contains(*task_id) {
                    return false;
                }

                // Check if all dependencies are complete
                if let Some(deps) = self.dependencies.get(*task_id) {
                    deps.iter().all(|dep| completed.contains(dep))
                } else {
                    true // No dependencies means ready
                }
            })
            .map(|id| id.to_string())
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_dag_rejected() {
        // FR-037: Empty DAG must be rejected
        let yaml = r#"
name: Empty Workflow
tasks: []
"#;

        let result = WorkflowDAG::from_workflow(yaml);
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), DagError::EmptyDag));
    }

    #[test]
    fn test_simple_linear_dag() {
        // Simple A -> B -> C chain
        let yaml = r#"
name: Linear Workflow
tasks:
  - id: task-a
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"

  - id: task-c
    agent: mock-agent
    depends_on: [task-b]
    task:
      history:
        - messageId: msg-3
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task C"
"#;

        let dag = WorkflowDAG::from_workflow(yaml).expect("Failed to parse workflow");

        // Verify entry nodes
        let entry_nodes = dag.entry_nodes();
        assert_eq!(entry_nodes.len(), 1);
        assert!(entry_nodes.contains(&"task-a".to_string()));

        // Verify ready nodes when task-a completes
        let mut completed = HashSet::new();
        completed.insert("task-a".to_string());
        let ready = dag.ready_nodes(&completed);
        assert_eq!(ready.len(), 1);
        assert!(ready.contains(&"task-b".to_string()));

        // Verify ready nodes when task-b completes
        completed.insert("task-b".to_string());
        let ready = dag.ready_nodes(&completed);
        assert_eq!(ready.len(), 1);
        assert!(ready.contains(&"task-c".to_string()));
    }

    #[test]
    fn test_parallel_dag() {
        // A -> B and A -> C (parallel branches)
        let yaml = r#"
name: Parallel Workflow
tasks:
  - id: task-a
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"

  - id: task-c
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-3
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task C"
"#;

        let dag = WorkflowDAG::from_workflow(yaml).expect("Failed to parse workflow");

        // After task-a completes, both B and C should be ready (parallel execution)
        let mut completed = HashSet::new();
        completed.insert("task-a".to_string());
        let mut ready = dag.ready_nodes(&completed);
        ready.sort(); // Sort for deterministic comparison

        assert_eq!(ready.len(), 2);
        assert!(ready.contains(&"task-b".to_string()));
        assert!(ready.contains(&"task-c".to_string()));
    }

    #[test]
    fn test_diamond_dag() {
        // Diamond: A -> B -> D, A -> C -> D
        let yaml = r#"
name: Diamond Workflow
tasks:
  - id: task-a
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"

  - id: task-c
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-3
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task C"

  - id: task-d
    agent: mock-agent
    depends_on: [task-b, task-c]
    task:
      history:
        - messageId: msg-4
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task D"
"#;

        let dag = WorkflowDAG::from_workflow(yaml).expect("Failed to parse workflow");

        // After A completes, B and C are ready
        let mut completed = HashSet::new();
        completed.insert("task-a".to_string());
        let mut ready = dag.ready_nodes(&completed);
        ready.sort();
        assert_eq!(ready.len(), 2);
        assert!(ready.contains(&"task-b".to_string()));
        assert!(ready.contains(&"task-c".to_string()));

        // After B completes (but not C), D is NOT ready
        completed.insert("task-b".to_string());
        let ready = dag.ready_nodes(&completed);
        assert!(!ready.contains(&"task-d".to_string()));

        // After C also completes, D is ready
        completed.insert("task-c".to_string());
        let ready = dag.ready_nodes(&completed);
        assert_eq!(ready.len(), 1);
        assert!(ready.contains(&"task-d".to_string()));
    }

    #[test]
    fn test_cycle_detection_simple() {
        // FR-014: Detect simple A -> B -> A cycle
        let yaml = r#"
name: Circular Workflow
tasks:
  - id: task-a
    agent: mock-agent
    depends_on: [task-b]
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"
"#;

        let result = WorkflowDAG::from_workflow(yaml);
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), DagError::CyclicDependency(_)));
    }

    #[test]
    fn test_cycle_detection_complex() {
        // FR-014: Detect complex A -> B -> C -> A cycle
        let yaml = r#"
name: Complex Circular Workflow
tasks:
  - id: task-a
    agent: mock-agent
    depends_on: [task-c]
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"

  - id: task-c
    agent: mock-agent
    depends_on: [task-b]
    task:
      history:
        - messageId: msg-3
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task C"
"#;

        let result = WorkflowDAG::from_workflow(yaml);
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), DagError::CyclicDependency(_)));
    }

    #[test]
    fn test_nonexistent_dependency() {
        // Detect dependency on non-existent task
        let yaml = r#"
name: Invalid Dependency
tasks:
  - id: task-a
    agent: mock-agent
    depends_on: [task-nonexistent]
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"
"#;

        let result = WorkflowDAG::from_workflow(yaml);
        assert!(result.is_err());
        match result.unwrap_err() {
            DagError::NonexistentDependency { task, dependency } => {
                assert_eq!(task, "task-a");
                assert_eq!(dependency, "task-nonexistent");
            }
            _ => panic!("Expected NonexistentDependency error"),
        }
    }

    #[test]
    fn test_entry_nodes_multiple() {
        // Multiple entry nodes (no dependencies)
        let yaml = r#"
name: Multiple Entry Nodes
tasks:
  - id: task-a
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"

  - id: task-c
    agent: mock-agent
    depends_on: [task-a, task-b]
    task:
      history:
        - messageId: msg-3
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task C"
"#;

        let dag = WorkflowDAG::from_workflow(yaml).expect("Failed to parse workflow");

        let mut entry_nodes = dag.entry_nodes();
        entry_nodes.sort();

        assert_eq!(entry_nodes.len(), 2);
        assert!(entry_nodes.contains(&"task-a".to_string()));
        assert!(entry_nodes.contains(&"task-b".to_string()));
    }

    #[test]
    fn test_ready_nodes_filters_completed() {
        // Ensure ready_nodes doesn't return already-completed tasks
        let yaml = r#"
name: Simple Workflow
tasks:
  - id: task-a
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task A"

  - id: task-b
    agent: mock-agent
    depends_on: [task-a]
    task:
      history:
        - messageId: msg-2
          kind: message
          role: user
          parts:
            - kind: text
              text: "Task B"
"#;

        let dag = WorkflowDAG::from_workflow(yaml).expect("Failed to parse workflow");

        let mut completed = HashSet::new();
        completed.insert("task-a".to_string());
        completed.insert("task-b".to_string());

        // Both tasks completed, should return empty
        let ready = dag.ready_nodes(&completed);
        assert!(ready.is_empty());
    }
}
