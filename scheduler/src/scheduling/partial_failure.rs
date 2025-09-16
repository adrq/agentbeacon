use common::dag::WorkflowDAG;
use std::collections::{HashSet, VecDeque};

/// Check if candidate_node has a dependency path to failed_node
///
/// Returns true if there's a directed path from failed_node to candidate_node
/// (meaning candidate_node transitively depends on failed_node and should be blocked)
pub fn has_dependency_path(failed_node: &str, candidate_node: &str, dag: &WorkflowDAG) -> bool {
    // BFS/DFS traversal from failed_node following dependents edges
    let mut visited = HashSet::new();
    let mut stack = vec![failed_node.to_string()];

    while let Some(node) = stack.pop() {
        if visited.contains(&node) {
            continue;
        }
        visited.insert(node.clone());

        // If we reached the candidate, there's a dependency path
        if node == candidate_node {
            return true;
        }

        // Add all nodes that depend on this node to the stack
        if let Some(deps) = dag.dependents.get(&node) {
            for dep in deps {
                if !visited.contains(dep) {
                    stack.push(dep.clone());
                }
            }
        }
    }

    false
}

/// Find all descendants of a node in O(N) using BFS
pub fn find_all_descendants(node_id: &str, dag: &WorkflowDAG) -> HashSet<String> {
    let mut descendants = HashSet::new();
    let mut queue = VecDeque::new();
    queue.push_back(node_id.to_string());

    while let Some(current) = queue.pop_front() {
        if let Some(dependents) = dag.dependents.get(&current) {
            for dep in dependents {
                // Only process if not already visited (prevents cycles)
                if descendants.insert(dep.clone()) {
                    queue.push_back(dep.clone());
                }
            }
        }
    }

    descendants
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_diamond_dag() -> WorkflowDAG {
        // A → B → D, A → C → D
        let yaml = r#"
name: Diamond DAG
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
        WorkflowDAG::from_workflow(yaml).expect("Failed to create diamond DAG")
    }

    fn create_independent_branches_dag() -> WorkflowDAG {
        // A → B, A → C (independent branches)
        let yaml = r#"
name: Independent Branches
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
        WorkflowDAG::from_workflow(yaml).expect("Failed to create independent branches DAG")
    }

    fn create_dependent_chain_dag() -> WorkflowDAG {
        // A → B → C (linear chain)
        let yaml = r#"
name: Dependent Chain
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
        WorkflowDAG::from_workflow(yaml).expect("Failed to create dependent chain DAG")
    }

    #[test]
    fn test_diamond_dag_partial_failure() {
        let dag = create_diamond_dag();

        // When B fails:
        // - C is independent (no path from B to C)
        // - D is blocked (path B → D exists)
        assert!(
            !has_dependency_path("task-b", "task-c", &dag),
            "task-c should be independent of failed task-b"
        );

        assert!(
            has_dependency_path("task-b", "task-d", &dag),
            "task-d should be blocked by failed task-b"
        );

        // When C fails:
        // - B is independent (no path from C to B)
        // - D is blocked (path C → D exists)
        assert!(
            !has_dependency_path("task-c", "task-b", &dag),
            "task-b should be independent of failed task-c"
        );

        assert!(
            has_dependency_path("task-c", "task-d", &dag),
            "task-d should be blocked by failed task-c"
        );
    }

    #[test]
    fn test_independent_branches() {
        let dag = create_independent_branches_dag();

        // When B fails, C is independent (no path from B to C)
        assert!(
            !has_dependency_path("task-b", "task-c", &dag),
            "task-c should be independent of failed task-b"
        );

        // When C fails, B is independent (no path from C to B)
        assert!(
            !has_dependency_path("task-c", "task-b", &dag),
            "task-b should be independent of failed task-c"
        );
    }

    #[test]
    fn test_dependent_chain() {
        let dag = create_dependent_chain_dag();

        // When B fails, C is blocked (path B → C exists)
        assert!(
            has_dependency_path("task-b", "task-c", &dag),
            "task-c should be blocked by failed task-b"
        );

        // When A fails, both B and C are blocked
        assert!(
            has_dependency_path("task-a", "task-b", &dag),
            "task-b should be blocked by failed task-a"
        );

        assert!(
            has_dependency_path("task-a", "task-c", &dag),
            "task-c should be blocked by failed task-a (transitive)"
        );
    }

    #[test]
    fn test_self_dependency_check() {
        let dag = create_diamond_dag();

        // A node is not considered to have a dependency path to itself for blocking purposes
        // (it's already failed, no need to check)
        assert!(
            has_dependency_path("task-a", "task-a", &dag),
            "Self-check should return true (node found in traversal)"
        );
    }

    #[test]
    fn test_no_dependency_path_to_upstream() {
        let dag = create_dependent_chain_dag();

        // Downstream nodes cannot block upstream nodes
        assert!(
            !has_dependency_path("task-c", "task-a", &dag),
            "Upstream task-a should be independent of downstream task-c"
        );

        assert!(
            !has_dependency_path("task-b", "task-a", &dag),
            "Upstream task-a should be independent of downstream task-b"
        );
    }
}
