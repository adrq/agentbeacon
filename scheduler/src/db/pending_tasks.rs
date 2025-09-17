use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;
use sqlx::Row;

/// Insert pending task to database
pub async fn insert_pending_task(
    pool: &DbPool,
    task: &TaskAssignment,
) -> Result<(), SchedulerError> {
    let task_json = serde_json::to_string(task)
        .map_err(|e| SchedulerError::Database(format!("Failed to serialize task: {e}")))?;

    let query = if pool.is_postgres() {
        "INSERT INTO pending_tasks (execution_id, node_id, task_assignment) VALUES ($1, $2, $3)"
    } else {
        "INSERT INTO pending_tasks (execution_id, node_id, task_assignment) VALUES (?, ?, ?)"
    };

    sqlx::query(query)
        .bind(&task.execution_id)
        .bind(&task.node_id)
        .bind(&task_json)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!(
                "Failed to insert pending task {}/{}: {}",
                task.execution_id, task.node_id, e
            ))
        })?;

    Ok(())
}

/// Delete pending task from database
pub async fn delete_pending_task(
    pool: &DbPool,
    execution_id: &str,
    node_id: &str,
) -> Result<(), SchedulerError> {
    let query = if pool.is_postgres() {
        "DELETE FROM pending_tasks WHERE execution_id = $1 AND node_id = $2"
    } else {
        "DELETE FROM pending_tasks WHERE execution_id = ? AND node_id = ?"
    };

    sqlx::query(query)
        .bind(execution_id)
        .bind(node_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!(
                "Failed to delete pending task {execution_id}/{node_id}: {e}"
            ))
        })?;

    Ok(())
}

/// List all pending tasks ordered by queued_at (FIFO)
pub async fn list_pending_tasks(pool: &DbPool) -> Result<Vec<TaskAssignment>, SchedulerError> {
    let query = "SELECT task_assignment FROM pending_tasks ORDER BY queued_at";

    let rows: Vec<(String,)> = sqlx::query_as(query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to list pending tasks: {e}")))?;

    let mut tasks = Vec::new();
    for (task_json,) in rows {
        let task: TaskAssignment = serde_json::from_str(&task_json)
            .map_err(|e| SchedulerError::Database(format!("Failed to deserialize task: {e}")))?;
        tasks.push(task);
    }

    Ok(tasks)
}

/// Pop task from queue transactionally (atomic SELECT + DELETE)
///
/// Uses database-native locking for safe concurrent access:
/// - PostgreSQL: FOR UPDATE SKIP LOCKED (lock-free concurrent access)
/// - SQLite: rows_affected() check detects concurrent deletions
///
/// Provides ACID guarantees: if transaction fails, task remains in queue.
/// The rows_affected() check prevents duplicate assignment even under concurrent load.
pub async fn pop_pending_task_transactional(
    pool: &DbPool,
) -> Result<Option<TaskAssignment>, SchedulerError> {
    // Begin transaction
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to begin transaction: {e}")))?;

    // SELECT first task with row-level locking
    let select_query = if pool.is_postgres() {
        // PostgreSQL: FOR UPDATE SKIP LOCKED allows concurrent workers to grab different tasks
        "SELECT execution_id, node_id, task_assignment
         FROM pending_tasks
         ORDER BY queued_at ASC
         LIMIT 1
         FOR UPDATE SKIP LOCKED"
    } else {
        // SQLite: No row-level locking, but rows_affected() check below handles races
        "SELECT execution_id, node_id, task_assignment
         FROM pending_tasks
         ORDER BY queued_at ASC
         LIMIT 1"
    };

    let row = sqlx::query(select_query)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to select task: {e}")))?;

    if let Some(row) = row {
        let execution_id: String = row
            .try_get("execution_id")
            .map_err(|e| SchedulerError::Database(format!("Failed to get execution_id: {e}")))?;
        let node_id: String = row
            .try_get("node_id")
            .map_err(|e| SchedulerError::Database(format!("Failed to get node_id: {e}")))?;
        let task_json: String = row
            .try_get("task_assignment")
            .map_err(|e| SchedulerError::Database(format!("Failed to get task_assignment: {e}")))?;

        // Parse TaskAssignment before deleting (fail early if corrupted)
        let task: TaskAssignment = serde_json::from_str(&task_json)
            .map_err(|e| SchedulerError::Database(format!("Failed to deserialize task: {e}")))?;

        // Delete within same transaction
        let delete_query = if pool.is_postgres() {
            "DELETE FROM pending_tasks WHERE execution_id = $1 AND node_id = $2"
        } else {
            "DELETE FROM pending_tasks WHERE execution_id = ? AND node_id = ?"
        };

        let result = sqlx::query(delete_query)
            .bind(&execution_id)
            .bind(&node_id)
            .execute(&mut *tx)
            .await
            .map_err(|e| {
                SchedulerError::Database(format!(
                    "Failed to delete task {execution_id}/{node_id}: {e}"
                ))
            })?;

        // Check rows_affected to detect if another worker deleted this row (defensive check for SQLite)
        if result.rows_affected() == 0 {
            // Task was already deleted by another worker - rollback and return None
            tx.rollback().await.map_err(|e| {
                SchedulerError::Database(format!("Failed to rollback transaction: {e}"))
            })?;
            return Ok(None);
        }

        // Commit transaction (if this fails, transaction auto-rolls back)
        tx.commit()
            .await
            .map_err(|e| SchedulerError::Database(format!("Failed to commit transaction: {e}")))?;

        Ok(Some(task))
    } else {
        // No tasks in queue - rollback (no-op, but explicit)
        tx.rollback().await.map_err(|e| {
            SchedulerError::Database(format!("Failed to rollback transaction: {e}"))
        })?;

        Ok(None)
    }
}

/// Count pending tasks (for metrics/monitoring)
pub async fn count_pending_tasks(pool: &DbPool) -> Result<usize, SchedulerError> {
    let row = sqlx::query("SELECT COUNT(*) as count FROM pending_tasks")
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to count pending tasks: {e}")))?;

    let count: i64 = row
        .try_get("count")
        .map_err(|e| SchedulerError::Database(format!("Failed to get count: {e}")))?;

    Ok(count as usize)
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

    async fn create_test_execution(pool: &DbPool, execution_id: &str) {
        // Create a minimal workflow first (use unique name to avoid conflicts)
        let workflow_id = uuid::Uuid::new_v4();
        let workflow = crate::db::Workflow {
            id: workflow_id,
            name: format!("test-workflow-{workflow_id}"), // Unique name per workflow
            description: None,
            yaml_content: "name: test\ntasks: []".to_string(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        };
        crate::db::workflows::create(pool, &workflow)
            .await
            .expect("Failed to create workflow");

        // Create execution
        let exec_uuid =
            uuid::Uuid::parse_str(execution_id).unwrap_or_else(|_| uuid::Uuid::new_v4());

        sqlx::query(
            "INSERT INTO executions (id, workflow_id, status, task_states, created_at, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
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
                    "parts": [{"kind": "text", "text": "Test"}]
                }]
            }),
            workflow_registry_id: Some("team/test".to_string()),
            workflow_version: Some("v1.0.0".to_string()),
            workflow_ref: Some("team/test:v1.0.0".to_string()),
            protocol_metadata: None,
        }
    }

    #[tokio::test]
    async fn test_insert_and_list() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        let task1 = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        let task2 = create_test_task("00000000-0000-0000-0000-000000000001", "task-2");

        // Insert tasks
        insert_pending_task(&pool, &task1)
            .await
            .expect("Failed to insert task1");
        insert_pending_task(&pool, &task2)
            .await
            .expect("Failed to insert task2");

        // List tasks
        let tasks = list_pending_tasks(&pool)
            .await
            .expect("Failed to list tasks");

        assert_eq!(tasks.len(), 2);
        assert_eq!(tasks[0].node_id, "task-1");
        assert_eq!(tasks[1].node_id, "task-2");
    }

    #[tokio::test]
    async fn test_delete_task() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        let task = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");

        // Insert task
        insert_pending_task(&pool, &task)
            .await
            .expect("Failed to insert");

        // Verify task exists
        let tasks = list_pending_tasks(&pool).await.expect("Failed to list");
        assert_eq!(tasks.len(), 1);

        // Delete task
        delete_pending_task(&pool, "00000000-0000-0000-0000-000000000001", "task-1")
            .await
            .expect("Failed to delete");

        // Verify task deleted
        let tasks = list_pending_tasks(&pool).await.expect("Failed to list");
        assert_eq!(tasks.len(), 0);
    }

    #[tokio::test]
    async fn test_fifo_ordering() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        // Insert tasks with delays to ensure different timestamps
        let task1 = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        insert_pending_task(&pool, &task1)
            .await
            .expect("Failed to insert task1");

        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        let task2 = create_test_task("00000000-0000-0000-0000-000000000001", "task-2");
        insert_pending_task(&pool, &task2)
            .await
            .expect("Failed to insert task2");

        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        let task3 = create_test_task("00000000-0000-0000-0000-000000000001", "task-3");
        insert_pending_task(&pool, &task3)
            .await
            .expect("Failed to insert task3");

        // List tasks should return in FIFO order (by queued_at)
        let tasks = list_pending_tasks(&pool).await.expect("Failed to list");

        assert_eq!(tasks.len(), 3);
        assert_eq!(tasks[0].node_id, "task-1");
        assert_eq!(tasks[1].node_id, "task-2");
        assert_eq!(tasks[2].node_id, "task-3");
    }

    #[tokio::test]
    async fn test_task_serialization() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        let task = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");

        // Insert task
        insert_pending_task(&pool, &task)
            .await
            .expect("Failed to insert");

        // Retrieve task
        let tasks = list_pending_tasks(&pool).await.expect("Failed to list");

        assert_eq!(tasks.len(), 1);
        let retrieved = &tasks[0];

        // Verify all fields preserved
        assert_eq!(retrieved.execution_id, task.execution_id);
        assert_eq!(retrieved.node_id, task.node_id);
        assert_eq!(retrieved.agent, task.agent);
        assert_eq!(retrieved.workflow_registry_id, task.workflow_registry_id);
        assert_eq!(retrieved.workflow_version, task.workflow_version);
        assert_eq!(retrieved.workflow_ref, task.workflow_ref);
    }

    #[tokio::test]
    async fn test_pop_transactional_success() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        let task = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");

        // Insert task
        insert_pending_task(&pool, &task)
            .await
            .expect("Failed to insert");

        // Verify task is in database
        let count_before = list_pending_tasks(&pool).await.expect("List failed").len();
        assert_eq!(count_before, 1);

        // Pop should succeed
        let popped = pop_pending_task_transactional(&pool)
            .await
            .expect("Pop failed");
        assert!(popped.is_some());

        let popped_task = popped.unwrap();
        assert_eq!(popped_task.execution_id, task.execution_id);
        assert_eq!(popped_task.node_id, task.node_id);

        // Verify task is removed from database
        let count_after = list_pending_tasks(&pool).await.expect("List failed").len();
        assert_eq!(count_after, 0);
    }

    #[tokio::test]
    async fn test_pop_transactional_empty_queue() {
        let pool = create_test_pool().await;

        // Pop from empty queue should return None
        let popped = pop_pending_task_transactional(&pool)
            .await
            .expect("Pop failed");
        assert!(popped.is_none());
    }

    #[tokio::test]
    async fn test_pop_transactional_fifo() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        // Insert three tasks with delays to ensure different timestamps
        let task1 = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        insert_pending_task(&pool, &task1)
            .await
            .expect("Failed to insert task1");

        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        let task2 = create_test_task("00000000-0000-0000-0000-000000000001", "task-2");
        insert_pending_task(&pool, &task2)
            .await
            .expect("Failed to insert task2");

        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        let task3 = create_test_task("00000000-0000-0000-0000-000000000001", "task-3");
        insert_pending_task(&pool, &task3)
            .await
            .expect("Failed to insert task3");

        // Pop tasks should return in FIFO order
        let popped1 = pop_pending_task_transactional(&pool)
            .await
            .expect("Pop 1 failed")
            .expect("Queue empty");
        assert_eq!(popped1.node_id, "task-1");

        let popped2 = pop_pending_task_transactional(&pool)
            .await
            .expect("Pop 2 failed")
            .expect("Queue empty");
        assert_eq!(popped2.node_id, "task-2");

        let popped3 = pop_pending_task_transactional(&pool)
            .await
            .expect("Pop 3 failed")
            .expect("Queue empty");
        assert_eq!(popped3.node_id, "task-3");

        // Queue should now be empty
        let popped4 = pop_pending_task_transactional(&pool)
            .await
            .expect("Pop 4 failed");
        assert!(popped4.is_none());
    }

    #[tokio::test]
    async fn test_count_pending_tasks() {
        let pool = create_test_pool().await;

        // Create execution record
        create_test_execution(&pool, "00000000-0000-0000-0000-000000000001").await;

        // Initially empty
        let count = count_pending_tasks(&pool).await.expect("Count failed");
        assert_eq!(count, 0);

        // Insert tasks
        let task1 = create_test_task("00000000-0000-0000-0000-000000000001", "task-1");
        insert_pending_task(&pool, &task1)
            .await
            .expect("Failed to insert");

        let count = count_pending_tasks(&pool).await.expect("Count failed");
        assert_eq!(count, 1);

        let task2 = create_test_task("00000000-0000-0000-0000-000000000001", "task-2");
        insert_pending_task(&pool, &task2)
            .await
            .expect("Failed to insert");

        let count = count_pending_tasks(&pool).await.expect("Count failed");
        assert_eq!(count, 2);

        // Pop one
        pop_pending_task_transactional(&pool)
            .await
            .expect("Pop failed");

        let count = count_pending_tasks(&pool).await.expect("Count failed");
        assert_eq!(count, 1);
    }
}
