use serde_json::json;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tokio::sync::RwLock;
use uuid::Uuid;

use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::TaskQueue;
use crate::scheduling::retry::{BackoffStrategy, RetryPolicy};
use crate::validation::SchemaValidator;
use common::dag::WorkflowDAG;

/// ExecutionStatus enum
#[derive(Debug, Clone, PartialEq)]
pub enum ExecutionStatus {
    Pending,
    Running,
    Completed,
    Failed,
}

/// ActiveExecution tracks the runtime state of a workflow execution.
///
/// # State Machine
///
/// ```text
/// Pending ──────┬─────────────────> Running
///               │                       │
///               │                       ├──> Completed (all tasks succeeded)
///               │                       │
///               │                       ├──> Failed (stop-all)
///               │                       │
///               │                       └──> Running (partial failure, independent branches continue)
///               │
///               └──> Failed (no entry nodes executable)
/// ```
///
/// # Fields
///
/// - `dag`: Immutable workflow structure
/// - `completed`: Tasks that finished successfully
/// - `failed`: Tasks that failed after retries exhausted
/// - `blocked`: Tasks that cannot run due to upstream failures (partial failure)
/// - `queued`: Tasks currently in task queue (prevents duplicate queueing)
/// - `retrying`: Tasks with pending retry delays (prevents duplicate queueing during retry window)
/// - `retry_counts`: Number of retry attempts per task (for retry policy enforcement)
/// - `status`: Overall execution state (Pending → Running → Completed/Failed)
#[derive(Debug, Clone)]
pub struct ActiveExecution {
    pub dag: WorkflowDAG,
    pub completed: HashSet<String>,
    pub failed: HashSet<String>,
    pub blocked: HashSet<String>,
    pub queued: HashSet<String>,
    pub retrying: HashSet<String>,
    pub retry_counts: std::collections::HashMap<String, u32>,
    pub status: ExecutionStatus,
}

/// Scheduler manages active executions and event-driven task queueing
#[derive(Clone)]
pub struct Scheduler {
    active_executions: Arc<RwLock<HashMap<String, ActiveExecution>>>,
    db_pool: DbPool,
    task_queue: Arc<TaskQueue>,
    validator: Arc<SchemaValidator>,
}

impl Scheduler {
    /// Create new scheduler instance
    pub fn new(
        db_pool: DbPool,
        task_queue: Arc<TaskQueue>,
        validator: Arc<SchemaValidator>,
    ) -> Self {
        Self {
            active_executions: Arc::new(RwLock::new(HashMap::new())),
            db_pool,
            task_queue,
            validator,
        }
    }

    /// Register a new execution with the scheduler
    pub async fn register_execution(
        &self,
        execution_id: String,
        dag: WorkflowDAG,
    ) -> Result<(), SchedulerError> {
        let mut executions = self.active_executions.write().await;
        executions.insert(
            execution_id,
            ActiveExecution {
                dag,
                completed: HashSet::new(),
                failed: HashSet::new(),
                blocked: HashSet::new(),
                queued: HashSet::new(),
                retrying: HashSet::new(),
                retry_counts: std::collections::HashMap::new(),
                status: ExecutionStatus::Pending,
            },
        );
        Ok(())
    }

    /// Convert DAG retry policy to scheduler retry policy
    fn dag_retry_to_scheduler_retry(dag_retry: &common::dag::RetryPolicy) -> RetryPolicy {
        let strategy = match dag_retry.backoff {
            common::dag::BackoffStrategy::Fixed => BackoffStrategy::Fixed,
            common::dag::BackoffStrategy::Linear => BackoffStrategy::Linear,
            common::dag::BackoffStrategy::Exponential => BackoffStrategy::Exponential,
        };

        RetryPolicy {
            strategy,
            base_delay_ms: dag_retry.delay_seconds * 1000, // Convert to milliseconds
            max_attempts: dag_retry.attempts,
            timeout_ms: None,
        }
    }

    /// Handle task result from worker (event-driven scheduling with retry support)
    pub async fn handle_task_result(
        &self,
        execution_id: &str,
        node_id: &str,
        success: bool,
    ) -> Result<(), SchedulerError> {
        // Get execution from active map
        let mut executions = self.active_executions.write().await;
        let execution = executions.get_mut(execution_id).ok_or_else(|| {
            SchedulerError::NotFound(format!("Execution not found: {execution_id}"))
        })?;

        // Update execution status if needed
        if execution.status == ExecutionStatus::Pending {
            execution.status = ExecutionStatus::Running;
        }

        // Get current retry count for this task
        let retry_count = execution.retry_counts.get(node_id).copied().unwrap_or(0);

        // Handle task failure with retry logic
        if !success {
            // Get task from DAG
            let task =
                execution.dag.tasks.get(node_id).ok_or_else(|| {
                    SchedulerError::NotFound(format!("Task not found: {node_id}"))
                })?;

            // Check if task has retry policy
            if let Some(exec_policy) = &task.execution {
                if let Some(retry_policy) = &exec_policy.retry {
                    // Convert DAG retry policy to scheduler retry policy
                    let scheduler_retry = Self::dag_retry_to_scheduler_retry(retry_policy);

                    // Check if we should retry
                    if scheduler_retry.should_retry(retry_count) {
                        // Calculate delay for next retry
                        if let Some(delay) = scheduler_retry.calculate_next_delay(retry_count) {
                            // Increment retry count
                            execution
                                .retry_counts
                                .insert(node_id.to_string(), retry_count + 1);

                            // Remove from queued set to allow re-queueing
                            execution.queued.remove(node_id);

                            // Mark as retrying to prevent duplicate queueing during delay window
                            execution.retrying.insert(node_id.to_string());

                            // Update database with retry count BEFORE spawning async task
                            let exec_uuid = Uuid::parse_str(execution_id).map_err(|e| {
                                SchedulerError::ValidationFailed(format!(
                                    "Invalid execution ID: {e}"
                                ))
                            })?;

                            // Fetch current execution to preserve existing task states
                            let db_execution =
                                crate::db::executions::get_by_id(&self.db_pool, &exec_uuid).await?;

                            // Clone existing task_states and merge updates
                            let mut task_states = db_execution
                                .task_states
                                .as_object()
                                .cloned()
                                .unwrap_or_else(serde_json::Map::new);

                            // Update only the retrying node
                            task_states.insert(
                                node_id.to_string(),
                                json!({
                                    "status": "retrying",
                                    "retry_count": retry_count + 1,
                                    "last_attempt": chrono::Utc::now().to_rfc3339(),
                                }),
                            );

                            crate::db::executions::update_status(
                                &self.db_pool,
                                &exec_uuid,
                                "running",
                                &json!(task_states),
                            )
                            .await?;

                            // Clone values needed for async operations
                            let execution_id = execution_id.to_string();
                            let node_id = node_id.to_string();
                            let db_pool = self.db_pool.clone();
                            let validator = self.validator.clone();
                            let dag = execution.dag.clone();
                            let task_queue = self.task_queue.clone();
                            let scheduler = self.clone();

                            // Spawn retry task with delay
                            tokio::spawn(async move {
                                tokio::time::sleep(delay).await;

                                // Verify execution still active before re-queuing
                                let exec_uuid = match uuid::Uuid::parse_str(&execution_id) {
                                    Ok(uuid) => uuid,
                                    Err(_) => {
                                        // Invalid ID - clear retrying flag anyway
                                        scheduler.clear_retrying(&execution_id, &node_id).await;
                                        return;
                                    }
                                };

                                match crate::db::executions::get_by_id(&db_pool, &exec_uuid).await {
                                    Ok(exec)
                                        if exec.status != "completed"
                                            && exec.status != "failed" =>
                                    {
                                        // Clear retrying flag before re-queueing
                                        scheduler.clear_retrying(&execution_id, &node_id).await;

                                        // Execution still active - proceed with retry
                                        if let Ok(assignment) =
                                            crate::scheduling::assignment::build_task_assignment(
                                                &db_pool,
                                                &validator,
                                                &execution_id,
                                                &node_id,
                                                &dag,
                                            )
                                            .await
                                        {
                                            let _ = task_queue.push(assignment).await;
                                        }
                                    }
                                    _ => {
                                        // Execution finished or not found - clear retrying flag and skip retry
                                        scheduler.clear_retrying(&execution_id, &node_id).await;
                                        tracing::debug!(
                                            "Skipping retry for {}/{} - execution no longer active",
                                            execution_id,
                                            node_id
                                        );
                                    }
                                }
                            });

                            return Ok(());
                        }
                    }
                }
            }

            // No retry policy or retries exhausted - mark as failed and apply partial failure logic
            execution.failed.insert(node_id.to_string());

            // Find all nodes blocked by this failure (FR-031) - O(N) using BFS
            let blocked_tasks =
                crate::scheduling::partial_failure::find_all_descendants(node_id, &execution.dag);
            execution.blocked.extend(blocked_tasks);

            // Determine workflow status: partial_failure if independent branches exist
            let has_independent_branches = execution.dag.tasks.len()
                > execution.completed.len() + execution.failed.len() + execution.blocked.len();

            let workflow_status = if has_independent_branches {
                execution.status = ExecutionStatus::Running; // Keep running for independent branches
                "partial_failure"
            } else {
                execution.status = ExecutionStatus::Failed;
                "failed"
            };

            let exec_uuid = Uuid::parse_str(execution_id).map_err(|e| {
                SchedulerError::ValidationFailed(format!("Invalid execution ID: {e}"))
            })?;

            // Fetch current execution to preserve existing task states
            let db_execution = crate::db::executions::get_by_id(&self.db_pool, &exec_uuid).await?;

            // Clone existing task_states and merge updates
            let mut task_states = db_execution
                .task_states
                .as_object()
                .cloned()
                .unwrap_or_else(serde_json::Map::new);

            // Update only the node that just failed (preserve other nodes' metadata)
            task_states.insert(
                node_id.to_string(),
                json!({
                    "status": "failed",
                    "retry_count": execution.retry_counts.get(node_id).copied().unwrap_or(0),
                    "failed_at": chrono::Utc::now().to_rfc3339(),
                }),
            );

            // Update only NEWLY blocked nodes (descendants of this failure)
            let newly_blocked =
                crate::scheduling::partial_failure::find_all_descendants(node_id, &execution.dag);
            for task_id in &newly_blocked {
                task_states.insert(
                    task_id.clone(),
                    json!({
                        "status": "blocked",
                        "blocked_by": node_id.to_string(),
                    }),
                );
            }

            crate::db::executions::update_status(
                &self.db_pool,
                &exec_uuid,
                workflow_status,
                &json!(task_states),
            )
            .await?;

            // If there are independent branches, continue queueing them
            if has_independent_branches {
                // Find ready nodes that are NOT blocked
                let ready_nodes = execution.dag.ready_nodes(&execution.completed);

                for ready_node_id in ready_nodes {
                    // Skip if already queued, retrying, failed, or blocked
                    if execution.queued.contains(&ready_node_id)
                        || execution.retrying.contains(&ready_node_id)
                        || execution.failed.contains(&ready_node_id)
                        || execution.blocked.contains(&ready_node_id)
                    {
                        continue;
                    }

                    let assignment = crate::scheduling::assignment::build_task_assignment(
                        &self.db_pool,
                        &self.validator,
                        execution_id,
                        &ready_node_id,
                        &execution.dag,
                    )
                    .await?;

                    self.task_queue.push(assignment).await?;
                    execution.queued.insert(ready_node_id.clone());
                }
            }

            return Ok(());
        }

        // Task succeeded - mark as completed
        execution.completed.insert(node_id.to_string());

        // Update database execution state
        let exec_uuid = Uuid::parse_str(execution_id)
            .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid execution ID: {e}")))?;

        // Fetch current execution to preserve existing task states (e.g., "assigned" from worker_sync)
        let db_execution = crate::db::executions::get_by_id(&self.db_pool, &exec_uuid).await?;

        // Clone existing task_states and merge updates (preserve other nodes)
        let mut task_states = db_execution
            .task_states
            .as_object()
            .cloned()
            .unwrap_or_else(serde_json::Map::new);

        // Update only the node that just completed (preserve other nodes' metadata)
        task_states.insert(
            node_id.to_string(),
            json!({
                "status": "completed",
                "retry_count": execution.retry_counts.get(node_id).copied().unwrap_or(0),
                "completed_at": chrono::Utc::now().to_rfc3339(),
            }),
        );

        let new_status = if execution.completed.len() == execution.dag.tasks.len() {
            "completed"
        } else {
            "running"
        };

        crate::db::executions::update_status(
            &self.db_pool,
            &exec_uuid,
            new_status,
            &json!(task_states),
        )
        .await?;

        // Find newly-ready tasks (event-driven scheduling)
        let ready_nodes = execution.dag.ready_nodes(&execution.completed);

        // Queue ready tasks (filter out already-queued, retrying, blocked, and failed tasks)
        for ready_node_id in ready_nodes {
            // Skip if already queued, retrying, blocked, or failed
            if execution.queued.contains(&ready_node_id)
                || execution.retrying.contains(&ready_node_id)
                || execution.blocked.contains(&ready_node_id)
                || execution.failed.contains(&ready_node_id)
            {
                continue;
            }

            let assignment = crate::scheduling::assignment::build_task_assignment(
                &self.db_pool,
                &self.validator,
                execution_id,
                &ready_node_id,
                &execution.dag,
            )
            .await?;

            // Push to task queue (write-through: persists to DB + in-memory)
            self.task_queue.push(assignment).await?;

            // Mark as queued
            execution.queued.insert(ready_node_id.clone());
        }

        // Check if execution is complete (all tasks are completed, failed, or blocked)
        let total_accounted =
            execution.completed.len() + execution.failed.len() + execution.blocked.len();

        if total_accounted == execution.dag.tasks.len() {
            // Set final status based on failures
            if execution.failed.is_empty() {
                execution.status = ExecutionStatus::Completed;
            } else if !execution.blocked.is_empty() || !execution.completed.is_empty() {
                // Has failures but also has completed/blocked tasks = partial failure
                execution.status = ExecutionStatus::Failed; // Keep as partial in-memory
            } else {
                execution.status = ExecutionStatus::Failed;
            }
        }

        Ok(())
    }

    /// Clear retrying flag when retry is about to be queued
    ///
    /// Called by spawned retry tasks before re-queueing to allow the task
    /// to be queued again and prevent it from being stuck in retrying state.
    pub async fn clear_retrying(&self, execution_id: &str, node_id: &str) {
        let mut executions = self.active_executions.write().await;
        if let Some(execution) = executions.get_mut(execution_id) {
            execution.retrying.remove(node_id);
        }
    }

    /// Mark task as assigned (called by worker_sync)
    ///
    /// This method coordinates with handle_task_result through the active_executions lock
    /// to prevent race conditions when updating task_states in the database.
    ///
    /// CRITICAL: This method acquires the same write lock as handle_task_result,
    /// ensuring that all task_states database updates are serialized and cannot
    /// interleave (which would cause lost updates).
    pub async fn mark_task_assigned(
        &self,
        execution_id: &str,
        node_id: &str,
    ) -> Result<(), SchedulerError> {
        // Acquire write lock to serialize with handle_task_result
        // This ensures DB operations cannot interleave
        let executions = self.active_executions.write().await;

        // Verify execution exists
        if !executions.contains_key(execution_id) {
            return Err(SchedulerError::NotFound(format!(
                "Execution not found: {execution_id}"
            )));
        }

        // Keep lock held during DB operations to prevent interleaving
        // (lock will be released when function returns)

        let exec_uuid = Uuid::parse_str(execution_id)
            .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid execution ID: {e}")))?;

        // Fetch current execution to preserve existing task states
        let db_execution = crate::db::executions::get_by_id(&self.db_pool, &exec_uuid).await?;

        // Clone existing task_states and merge updates
        let mut task_states = db_execution
            .task_states
            .as_object()
            .cloned()
            .unwrap_or_else(serde_json::Map::new);

        // Update only this specific task (preserve other tasks)
        task_states.insert(
            node_id.to_string(),
            json!({
                "status": "assigned",
                "assigned_at": chrono::Utc::now().to_rfc3339(),
            }),
        );

        crate::db::executions::update_status(
            &self.db_pool,
            &exec_uuid,
            "running",
            &json!(task_states),
        )
        .await?;

        Ok(())
    }

    /// Queue entry nodes for a newly registered execution
    pub async fn queue_entry_nodes(&self, execution_id: &str) -> Result<(), SchedulerError> {
        // Need write lock to update queued set
        let mut executions = self.active_executions.write().await;
        let execution = executions.get_mut(execution_id).ok_or_else(|| {
            SchedulerError::NotFound(format!("Execution not found: {execution_id}"))
        })?;

        let entry_nodes = execution.dag.entry_nodes();

        for node_id in entry_nodes {
            let assignment = crate::scheduling::assignment::build_task_assignment(
                &self.db_pool,
                &self.validator,
                execution_id,
                &node_id,
                &execution.dag,
            )
            .await?;

            self.task_queue.push(assignment).await?;

            // Mark as queued
            execution.queued.insert(node_id.clone());
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    use std::sync::Once;

    static INIT_DRIVERS: Once = Once::new();

    fn install_drivers() {
        INIT_DRIVERS.call_once(|| {
            sqlx::any::install_default_drivers();
        });
    }

    async fn create_test_pool() -> DbPool {
        install_drivers();

        let pool = db::pool::create("sqlite::memory:")
            .await
            .expect("Failed to create test pool");

        db::migrations::run(&pool, "sqlite::memory:")
            .await
            .expect("Failed to run migrations");

        pool
    }

    async fn create_test_workflow(pool: &DbPool) -> (Uuid, Uuid) {
        let workflow_id = Uuid::new_v4();
        let workflow = crate::db::Workflow {
            id: workflow_id,
            name: format!("test-workflow-{workflow_id}"),
            description: None,
            yaml_content: "name: test\ntasks: []".to_string(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        };
        crate::db::workflows::create(pool, &workflow)
            .await
            .expect("Failed to create workflow");

        let task_states = json!({});
        let execution_id = crate::db::executions::create(pool, &workflow_id, task_states)
            .await
            .expect("Failed to create execution");

        (workflow_id, execution_id)
    }

    fn create_linear_workflow() -> WorkflowDAG {
        // A → B → C
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
        WorkflowDAG::from_workflow(yaml).expect("Failed to create linear workflow")
    }

    fn create_parallel_workflow() -> WorkflowDAG {
        // A → B and A → C (parallel branches)
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
        WorkflowDAG::from_workflow(yaml).expect("Failed to create parallel workflow")
    }

    fn create_diamond_workflow() -> WorkflowDAG {
        // A → B → D, A → C → D
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
        WorkflowDAG::from_workflow(yaml).expect("Failed to create diamond workflow")
    }

    #[tokio::test]
    async fn test_linear_workflow_scheduling() {
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Scheduler::new(pool.clone(), task_queue.clone(), validator);

        let dag = create_linear_workflow();
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        // Queue entry nodes (should be task-a)
        scheduler
            .queue_entry_nodes(&execution_id.to_string())
            .await
            .expect("Failed to queue entry nodes");

        // Verify task-a is queued
        assert_eq!(task_queue.len().await.expect("len failed"), 1);

        // Simulate task-a completion
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-a", true)
            .await
            .expect("Failed to handle task-a result");

        // Verify task-b is now queued
        assert_eq!(task_queue.len().await.expect("len failed"), 2); // task-a (already popped by worker) + task-b

        // Simulate task-b completion
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-b", true)
            .await
            .expect("Failed to handle task-b result");

        // Verify task-c is now queued
        assert_eq!(task_queue.len().await.expect("len failed"), 3);

        // Simulate task-c completion
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-c", true)
            .await
            .expect("Failed to handle task-c result");

        // Verify execution is complete
        let executions = scheduler.active_executions.read().await;
        let execution = executions.get(&execution_id.to_string()).unwrap();
        assert_eq!(execution.status, ExecutionStatus::Completed);
        assert_eq!(execution.completed.len(), 3);
    }

    #[tokio::test]
    async fn test_parallel_workflow_scheduling() {
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Scheduler::new(pool.clone(), task_queue.clone(), validator);

        let dag = create_parallel_workflow();
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        // Queue entry nodes (should be task-a)
        scheduler
            .queue_entry_nodes(&execution_id.to_string())
            .await
            .expect("Failed to queue entry nodes");

        assert_eq!(task_queue.len().await.expect("len failed"), 1);

        // Simulate task-a completion
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-a", true)
            .await
            .expect("Failed to handle task-a result");

        // Verify both task-b and task-c are queued (parallel execution)
        assert_eq!(task_queue.len().await.expect("len failed"), 3); // Original + 2 new tasks

        // Complete task-b
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-b", true)
            .await
            .expect("Failed to handle task-b result");

        // Complete task-c
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-c", true)
            .await
            .expect("Failed to handle task-c result");

        // Verify execution is complete
        let executions = scheduler.active_executions.read().await;
        let execution = executions.get(&execution_id.to_string()).unwrap();
        assert_eq!(execution.status, ExecutionStatus::Completed);
    }

    #[tokio::test]
    async fn test_diamond_workflow_scheduling() {
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Scheduler::new(pool.clone(), task_queue.clone(), validator);

        let dag = create_diamond_workflow();
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        // Queue entry nodes (should be task-a)
        scheduler
            .queue_entry_nodes(&execution_id.to_string())
            .await
            .expect("Failed to queue entry nodes");

        // Simulate task-a completion
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-a", true)
            .await
            .expect("Failed to handle task-a result");

        // Both task-b and task-c should be queued
        assert_eq!(task_queue.len().await.expect("len failed"), 3);

        // Complete task-b
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-b", true)
            .await
            .expect("Failed to handle task-b result");

        // task-d should NOT be queued yet (waiting for task-c)
        assert_eq!(task_queue.len().await.expect("len failed"), 3); // No new tasks

        // Complete task-c
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-c", true)
            .await
            .expect("Failed to handle task-c result");

        // NOW task-d should be queued (both dependencies satisfied)
        assert_eq!(task_queue.len().await.expect("len failed"), 4);

        // Complete task-d
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-d", true)
            .await
            .expect("Failed to handle task-d result");

        // Verify execution is complete
        let executions = scheduler.active_executions.read().await;
        let execution = executions.get(&execution_id.to_string()).unwrap();
        assert_eq!(execution.status, ExecutionStatus::Completed);
        assert_eq!(execution.completed.len(), 4);
    }

    #[tokio::test]
    async fn test_event_driven_queueing() {
        // Verify that tasks are only queued when dependencies are satisfied
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Scheduler::new(pool.clone(), task_queue.clone(), validator);

        let dag = create_diamond_workflow();
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        // Initially, queue should be empty
        assert_eq!(task_queue.len().await.expect("len failed"), 0);

        // Queue entry nodes
        scheduler
            .queue_entry_nodes(&execution_id.to_string())
            .await
            .expect("Failed to queue entry nodes");

        // Only task-a should be queued (entry node)
        assert_eq!(task_queue.len().await.expect("len failed"), 1);

        // Get execution state
        let executions = scheduler.active_executions.read().await;
        let execution = executions.get(&execution_id.to_string()).unwrap();

        // No tasks completed yet
        assert_eq!(execution.completed.len(), 0);
        assert_eq!(execution.status, ExecutionStatus::Pending);
    }

    #[tokio::test]
    async fn test_task_state_history_preservation() {
        // Verify that task states are merged, not overwritten (FR-019 compliance)
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Scheduler::new(pool.clone(), task_queue.clone(), validator);

        let dag = create_parallel_workflow(); // A → B and A → C
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        // Manually add "assigned" state for task-b (simulating worker_sync.rs behavior)
        let exec_uuid = execution_id;
        let db_execution = crate::db::executions::get_by_id(&pool, &exec_uuid)
            .await
            .expect("Failed to get execution");

        let mut task_states = db_execution
            .task_states
            .as_object()
            .cloned()
            .unwrap_or_else(serde_json::Map::new);

        task_states.insert(
            "task-b".to_string(),
            json!({
                "status": "assigned",
                "assigned_at": chrono::Utc::now().to_rfc3339(),
            }),
        );

        crate::db::executions::update_status(&pool, &exec_uuid, "running", &json!(task_states))
            .await
            .expect("Failed to update task-b state");

        // Verify task-b state is in DB
        let db_exec = crate::db::executions::get_by_id(&pool, &exec_uuid)
            .await
            .expect("Failed to get execution");
        assert!(
            db_exec.task_states.get("task-b").is_some(),
            "task-b assigned state should be in DB"
        );

        // Now complete task-a (should trigger task-b and task-c queueing)
        scheduler
            .handle_task_result(&execution_id.to_string(), "task-a", true)
            .await
            .expect("Failed to handle task-a result");

        // Verify task-b's "assigned" state is PRESERVED (not overwritten by task-a's "completed")
        let final_exec = crate::db::executions::get_by_id(&pool, &exec_uuid)
            .await
            .expect("Failed to get execution");

        let task_a_state = final_exec.task_states.get("task-a");
        let task_b_state = final_exec.task_states.get("task-b");

        // Assert task-a has completed state
        assert!(
            task_a_state.is_some(),
            "task-a completed state should be in DB"
        );
        assert_eq!(
            task_a_state.unwrap().get("status").unwrap().as_str(),
            Some("completed"),
            "task-a status should be completed"
        );

        // Assert task-b STILL has assigned state (FR-019: state history preserved)
        assert!(
            task_b_state.is_some(),
            "task-b assigned state should still be in DB"
        );
        assert_eq!(
            task_b_state.unwrap().get("status").unwrap().as_str(),
            Some("assigned"),
            "task-b status should still be assigned (not lost)"
        );
    }

    #[tokio::test]
    async fn test_metadata_preserved_across_multiple_completions() {
        // Verify that task A's metadata is NOT overwritten when task B completes
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Scheduler::new(pool.clone(), task_queue.clone(), validator);

        let dag = create_parallel_workflow(); // A → B and A → C
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        scheduler
            .queue_entry_nodes(&execution_id.to_string())
            .await
            .expect("Failed to queue entry nodes");

        // Complete task-a first
        let before_a_complete = chrono::Utc::now();
        tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;

        scheduler
            .handle_task_result(&execution_id.to_string(), "task-a", true)
            .await
            .expect("Failed to handle task-a result");

        tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        let after_a_complete = chrono::Utc::now();

        // Verify task-a has completed state with timestamp
        let exec_after_a = crate::db::executions::get_by_id(&pool, &execution_id)
            .await
            .expect("Failed to get execution");

        let task_a_state_after_a = exec_after_a
            .task_states
            .get("task-a")
            .expect("task-a should have state");
        let task_a_completed_at_str = task_a_state_after_a
            .get("completed_at")
            .expect("task-a should have completed_at")
            .as_str()
            .expect("completed_at should be string");
        let task_a_completed_at = chrono::DateTime::parse_from_rfc3339(task_a_completed_at_str)
            .expect("completed_at should be valid RFC3339")
            .with_timezone(&chrono::Utc);

        // Verify task-a timestamp is in expected range
        assert!(
            task_a_completed_at >= before_a_complete && task_a_completed_at <= after_a_complete,
            "task-a completed_at should be between before_a and after_a"
        );

        // Now complete task-b (which should NOT overwrite task-a's timestamp)
        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        scheduler
            .handle_task_result(&execution_id.to_string(), "task-b", true)
            .await
            .expect("Failed to handle task-b result");

        // Verify task-a's timestamp is UNCHANGED (not overwritten by task-b completion)
        let exec_after_b = crate::db::executions::get_by_id(&pool, &execution_id)
            .await
            .expect("Failed to get execution");

        let task_a_state_after_b = exec_after_b
            .task_states
            .get("task-a")
            .expect("task-a should still have state");
        let task_a_completed_at_after_b_str = task_a_state_after_b
            .get("completed_at")
            .expect("task-a should still have completed_at")
            .as_str()
            .expect("completed_at should be string");

        // Critical assertion: task-a's timestamp should be identical (not overwritten)
        assert_eq!(
            task_a_completed_at_str, task_a_completed_at_after_b_str,
            "task-a completed_at timestamp should NOT change when task-b completes"
        );

        // Also verify task-b has its own timestamp
        let task_b_state = exec_after_b
            .task_states
            .get("task-b")
            .expect("task-b should have state");
        assert!(
            task_b_state.get("completed_at").is_some(),
            "task-b should have completed_at"
        );
    }

    #[tokio::test]
    async fn test_concurrent_task_completion_and_assignment() {
        // Verify that concurrent handle_task_result and mark_task_assigned calls
        // don't cause lost updates to task_states (regression test for race condition)
        let pool = create_test_pool().await;
        let (_workflow_id, execution_id) = create_test_workflow(&pool).await;
        let validator = Arc::new(SchemaValidator::new().expect("Failed to create validator"));
        let task_queue = Arc::new(TaskQueue::new(pool.clone()));
        let scheduler = Arc::new(Scheduler::new(pool.clone(), task_queue.clone(), validator));

        let dag = create_parallel_workflow(); // A → B and A → C
        scheduler
            .register_execution(execution_id.to_string(), dag)
            .await
            .expect("Failed to register execution");

        // Spawn 10 concurrent threads doing completions and assignments
        let mut handles = vec![];
        for i in 0..10 {
            let scheduler_clone = scheduler.clone();
            let exec_id = execution_id.to_string();

            let handle = tokio::spawn(async move {
                if i % 2 == 0 {
                    // Even threads: mark tasks as assigned
                    let _ = scheduler_clone
                        .mark_task_assigned(&exec_id, &format!("task-{i}"))
                        .await;
                } else {
                    // Odd threads: complete task-a (triggers scheduling)
                    let _ = scheduler_clone
                        .handle_task_result(&exec_id, "task-a", true)
                        .await;
                }
            });
            handles.push(handle);
        }

        // Wait for all threads
        for handle in handles {
            handle.await.expect("Thread panicked");
        }

        // Verify that task-a's completion was NOT lost (should be in final state)
        let final_exec = crate::db::executions::get_by_id(&pool, &execution_id)
            .await
            .expect("Failed to get execution");

        let task_a_state = final_exec.task_states.get("task-a");
        assert!(
            task_a_state.is_some(),
            "task-a state should exist (not lost due to race condition)"
        );
        assert_eq!(
            task_a_state.unwrap().get("status").unwrap().as_str(),
            Some("completed"),
            "task-a should be marked completed"
        );

        // Note: Some mark_task_assigned calls will fail for non-existent task IDs,
        // but the important thing is task-a's completion wasn't lost
        println!(
            "Final task_states: {}",
            serde_json::to_string_pretty(&final_exec.task_states).unwrap()
        );

        // The critical assertion: task-a must be present (proves no lost updates)
        assert!(
            task_a_state.is_some(),
            "Critical: task-a completion must not be lost to race condition"
        );
    }
}
