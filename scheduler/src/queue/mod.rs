use serde::{Deserialize, Serialize};

use crate::db::DbPool;
use crate::error::SchedulerError;

/// TaskAssignment represents a task ready for worker execution
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskAssignment {
    pub execution_id: String,
    pub node_id: String,
    pub agent: String,
    pub task: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_registry_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_ref: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_metadata: Option<serde_json::Value>,
}

/// TaskQueue provides FIFO task assignment with transactional database operations
///
/// This implementation uses the database as the single source of truth, eliminating
/// the complexity and failure modes of write-through caching. All operations are
/// transactional, providing ACID guarantees.
///
/// Performance: 1-5ms database latency per operation is negligible compared to
/// typical task execution times (10-60 seconds for AI agents).
pub struct TaskQueue {
    db_pool: DbPool,
}

impl TaskQueue {
    /// Create new TaskQueue with database pool
    pub fn new(db_pool: DbPool) -> Self {
        Self { db_pool }
    }

    /// Push task to queue (persisted to database)
    pub async fn push(&self, task: TaskAssignment) -> Result<(), SchedulerError> {
        crate::db::pending_tasks::insert_pending_task(&self.db_pool, &task).await
    }

    /// Pop task from queue transactionally (FIFO, atomic)
    ///
    /// Uses database-native locking for safe concurrent access:
    /// - PostgreSQL: FOR UPDATE SKIP LOCKED (lock-free concurrent workers)
    /// - SQLite: BEGIN IMMEDIATE (exclusive lock, ~300μs duration)
    ///
    /// If the transaction fails, the task remains in the queue (ACID guarantees).
    pub async fn pop(&self) -> Result<Option<TaskAssignment>, SchedulerError> {
        crate::db::pending_tasks::pop_pending_task_transactional(&self.db_pool).await
    }

    /// Get current queue length
    pub async fn len(&self) -> Result<usize, SchedulerError> {
        crate::db::pending_tasks::count_pending_tasks(&self.db_pool).await
    }

    /// Check if queue is empty
    pub async fn is_empty(&self) -> Result<bool, SchedulerError> {
        Ok(self.len().await? == 0)
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
        db::pool::create("sqlite::memory:")
            .await
            .expect("Failed to create test pool")
    }

    async fn setup_test_queue() -> TaskQueue {
        let pool = create_test_pool().await;
        db::migrations::run(&pool, "sqlite::memory:")
            .await
            .expect("Failed to run migrations");
        TaskQueue::new(pool)
    }

    async fn create_test_execution(pool: &DbPool, execution_id: &str) {
        let workflow_id = uuid::Uuid::new_v4();
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

        let exec_uuid =
            uuid::Uuid::parse_str(execution_id).expect("Test execution_id must be valid UUID");
        let query = pool.prepare_query(
            "INSERT INTO executions (id, workflow_id, status, task_states, created_at, updated_at)
             VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        );
        sqlx::query(&query)
            .bind(exec_uuid.to_string())
            .bind(workflow_id.to_string())
            .bind("pending")
            .bind("{}")
            .execute(pool.as_ref())
            .await
            .expect("Failed to create execution");
    }

    fn create_test_task(execution_id: &str, node_id: &str) -> TaskAssignment {
        TaskAssignment {
            execution_id: execution_id.to_string(),
            node_id: node_id.to_string(),
            agent: "test-agent".to_string(),
            task: serde_json::json!({
                "history": [{
                    "messageId": "msg-1",
                    "kind": "message",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Test task"}]
                }]
            }),
            workflow_registry_id: None,
            workflow_version: None,
            workflow_ref: None,
            protocol_metadata: None,
        }
    }

    #[tokio::test]
    async fn test_fifo_ordering() {
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_execution(pool, "00000000-0000-0000-0000-000000000001").await;

        let task1 = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        let task2 = create_test_task("00000000-0000-0000-0000-000000000001", "task-2");
        let task3 = create_test_task("00000000-0000-0000-0000-000000000001", "task-3");

        queue.push(task1).await.expect("Failed to push task1");
        queue.push(task2).await.expect("Failed to push task2");
        queue.push(task3).await.expect("Failed to push task3");

        let popped1 = queue
            .pop()
            .await
            .expect("Failed to pop")
            .expect("Queue empty");
        assert_eq!(popped1.node_id, "task-1");

        let popped2 = queue
            .pop()
            .await
            .expect("Failed to pop")
            .expect("Queue empty");
        assert_eq!(popped2.node_id, "task-2");

        let popped3 = queue
            .pop()
            .await
            .expect("Failed to pop")
            .expect("Queue empty");
        assert_eq!(popped3.node_id, "task-3");

        assert!(queue.is_empty().await.expect("Failed to check empty"));
    }

    #[tokio::test]
    async fn test_concurrent_workers_no_duplicates() {
        let queue = std::sync::Arc::new(setup_test_queue().await);
        let pool = &queue.db_pool;

        for i in 0..5 {
            let exec_id = format!("0000000{i}-0000-0000-0000-000000000001");
            create_test_execution(pool, &exec_id).await;
        }

        // Push 5 tasks
        for i in 0..5 {
            let exec_id = format!("0000000{i}-0000-0000-0000-000000000001");
            let task = create_test_task(&exec_id, &format!("task-{i}"));
            queue.push(task).await.expect("Failed to push");
        }

        // Spawn 5 concurrent workers
        let mut handles = vec![];
        for _ in 0..5 {
            let queue_clone = queue.clone();
            let handle =
                tokio::spawn(async move { queue_clone.pop().await.expect("Failed to pop") });
            handles.push(handle);
        }

        // Collect results
        let mut results = vec![];
        for handle in handles {
            if let Some(task) = handle.await.expect("Worker panicked") {
                results.push(task);
            }
        }

        // Verify: exactly 5 unique tasks (no duplicates)
        assert_eq!(results.len(), 5, "Should receive exactly 5 tasks");

        let mut node_ids: Vec<_> = results.iter().map(|t| t.node_id.clone()).collect();
        node_ids.sort();
        node_ids.dedup();
        assert_eq!(
            node_ids.len(),
            5,
            "Should have 5 unique tasks (no duplicates)"
        );

        assert!(queue.is_empty().await.expect("Failed to check empty"));
    }

    #[tokio::test]
    async fn test_empty_queue_pop() {
        let queue = setup_test_queue().await;
        let result = queue.pop().await.expect("Failed to pop");
        assert!(result.is_none(), "Pop from empty queue should return None");
    }

    #[tokio::test]
    async fn test_transactional_safety() {
        // Verify database is always consistent (no lost tasks)
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_execution(pool, "00000000-0000-0000-0000-000000000001").await;

        let task = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        queue.push(task).await.expect("Failed to push");

        // Pop succeeds
        let popped = queue.pop().await.expect("Failed to pop");
        assert!(popped.is_some());

        // Verify task is removed from database
        let count = crate::db::pending_tasks::list_pending_tasks(pool)
            .await
            .expect("Failed to list")
            .len();
        assert_eq!(count, 0, "Task should be removed from database");
    }

    #[tokio::test]
    async fn test_len_and_is_empty() {
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_execution(pool, "00000000-0000-0000-0000-000000000001").await;

        // Initially empty
        assert!(queue.is_empty().await.expect("is_empty failed"));
        assert_eq!(queue.len().await.expect("len failed"), 0);

        // Push task
        let task = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        queue.push(task).await.expect("Failed to push");

        // Not empty
        assert!(!queue.is_empty().await.expect("is_empty failed"));
        assert_eq!(queue.len().await.expect("len failed"), 1);

        // Pop task
        queue.pop().await.expect("Failed to pop");

        // Empty again
        assert!(queue.is_empty().await.expect("is_empty failed"));
        assert_eq!(queue.len().await.expect("len failed"), 0);
    }

    #[tokio::test]
    async fn test_pop_rollback_on_parse_failure() {
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_execution(pool, "00000000-0000-0000-0000-000000000001").await;

        // Insert corrupted task JSON directly via SQL
        let corrupted_json = "{invalid json";
        let query = pool.prepare_query(
            "INSERT INTO pending_tasks (execution_id, node_id, task_assignment) VALUES (?, ?, ?)",
        );
        sqlx::query(&query)
            .bind("00000000-0000-0000-0000-000000000001")
            .bind("task-1")
            .bind(corrupted_json)
            .execute(pool.as_ref())
            .await
            .expect("Failed to insert corrupted task");

        // Pop should fail but transaction should rollback
        let result = queue.pop().await;
        assert!(result.is_err(), "Pop should fail on corrupted JSON");

        // Verify task is STILL in database (rollback succeeded)
        let query = pool.prepare_query("SELECT COUNT(*) FROM pending_tasks");
        let count: i64 = sqlx::query_scalar(&query)
            .fetch_one(pool.as_ref())
            .await
            .expect("Failed to count tasks");
        assert_eq!(count, 1, "Task should remain in DB after failed pop");
    }

    #[tokio::test]
    async fn test_push_idempotency() {
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_execution(pool, "00000000-0000-0000-0000-000000000001").await;

        let task = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");

        // First push should succeed
        queue
            .push(task.clone())
            .await
            .expect("First push should succeed");

        // Second push with same (execution_id, node_id) should fail
        let result = queue.push(task).await;
        assert!(
            result.is_err(),
            "Duplicate push should fail due to unique constraint"
        );

        // Verify only one task in queue
        let count = queue.len().await.expect("len failed");
        assert_eq!(count, 1, "Queue should contain exactly 1 task, not 2");
    }
}
