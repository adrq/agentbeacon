use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;
use sqlx::Row;

/// Insert task into queue
pub async fn insert(pool: &DbPool, task: &TaskAssignment) -> Result<(), SchedulerError> {
    let payload_json = serde_json::to_string(&task.task_payload)
        .map_err(|e| SchedulerError::Database(format!("serialize task_payload failed: {e}")))?;

    let query = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(&task.execution_id)
        .bind(&task.session_id)
        .bind(&payload_json)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!(
                "insert task_queue failed: {}/{}: {}",
                task.execution_id, task.session_id, e
            ))
        })?;

    Ok(())
}

/// Pop oldest task from queue (FIFO) transactionally
pub async fn pop(pool: &DbPool) -> Result<Option<TaskAssignment>, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let select_query = if pool.is_postgres() {
        "SELECT id, execution_id, session_id, task_payload
         FROM task_queue
         ORDER BY queued_at ASC
         LIMIT 1
         FOR UPDATE SKIP LOCKED"
    } else {
        "SELECT id, execution_id, session_id, task_payload
         FROM task_queue
         ORDER BY queued_at ASC
         LIMIT 1"
    };

    let row = sqlx::query(select_query)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("select task failed: {e}")))?;

    if let Some(row) = row {
        let row_id: i64 = row
            .try_get("id")
            .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?;
        let execution_id: String = row
            .try_get("execution_id")
            .map_err(|e| SchedulerError::Database(format!("get execution_id failed: {e}")))?;
        let session_id: String = row
            .try_get("session_id")
            .map_err(|e| SchedulerError::Database(format!("get session_id failed: {e}")))?;
        let payload_json: String = row
            .try_get("task_payload")
            .map_err(|e| SchedulerError::Database(format!("get task_payload failed: {e}")))?;

        let task_payload: serde_json::Value = serde_json::from_str(&payload_json).map_err(|e| {
            SchedulerError::Database(format!("deserialize task_payload failed: {e}"))
        })?;

        let delete_query = if pool.is_postgres() {
            "DELETE FROM task_queue WHERE id = $1"
        } else {
            "DELETE FROM task_queue WHERE id = ?"
        };

        let result = sqlx::query(delete_query)
            .bind(row_id)
            .execute(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("delete task failed: {e}")))?;

        if result.rows_affected() == 0 {
            tx.rollback().await.map_err(|e| {
                SchedulerError::Database(format!("rollback transaction failed: {e}"))
            })?;
            return Ok(None);
        }

        tx.commit()
            .await
            .map_err(|e| SchedulerError::Database(format!("commit transaction failed: {e}")))?;

        Ok(Some(TaskAssignment {
            execution_id,
            session_id,
            task_payload,
        }))
    } else {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback transaction failed: {e}")))?;
        Ok(None)
    }
}

/// Count tasks in queue
pub async fn count(pool: &DbPool) -> Result<usize, SchedulerError> {
    let row = sqlx::query("SELECT COUNT(*) as count FROM task_queue")
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count task_queue failed: {e}")))?;

    let count: i64 = row
        .try_get("count")
        .map_err(|e| SchedulerError::Database(format!("get count failed: {e}")))?;

    Ok(count as usize)
}
