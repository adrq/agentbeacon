use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;
use sqlx::Row;

/// Insert task into queue with optional source identifier
pub async fn insert(
    pool: &DbPool,
    task: &TaskAssignment,
    source: Option<&str>,
) -> Result<(), SchedulerError> {
    let payload_json = serde_json::to_string(&task.task_payload)
        .map_err(|e| SchedulerError::Database(format!("serialize task_payload failed: {e}")))?;

    let query = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(&task.execution_id)
        .bind(&task.session_id)
        .bind(&payload_json)
        .bind(source)
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

/// Pop oldest task for a specific session (per-session inbox)
pub async fn pop_by_session(
    pool: &DbPool,
    session_id: &str,
) -> Result<Option<TaskAssignment>, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let select_query = if pool.is_postgres() {
        "SELECT id, execution_id, session_id, task_payload
         FROM task_queue WHERE session_id = $1
         ORDER BY queued_at ASC
         LIMIT 1
         FOR UPDATE SKIP LOCKED"
    } else {
        "SELECT id, execution_id, session_id, task_payload
         FROM task_queue WHERE session_id = ?
         ORDER BY queued_at ASC
         LIMIT 1"
    };

    let row = sqlx::query(select_query)
        .bind(session_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("select task by session failed: {e}")))?;

    if let Some(row) = row {
        let row_id: i64 = row
            .try_get("id")
            .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?;
        let execution_id: String = row
            .try_get("execution_id")
            .map_err(|e| SchedulerError::Database(format!("get execution_id failed: {e}")))?;
        let sid: String = row
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
            session_id: sid,
            task_payload,
        }))
    } else {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback transaction failed: {e}")))?;
        Ok(None)
    }
}

/// Check if a task exists for a specific session (non-destructive peek)
pub async fn has_task_for_session(pool: &DbPool, session_id: &str) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query("SELECT 1 FROM task_queue WHERE session_id = ? LIMIT 1");

    let row = sqlx::query(&query)
        .bind(session_id)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("has_task_for_session failed: {e}")))?;

    Ok(row.is_some())
}

/// Delete all queued tasks for a specific session. Returns the number of rows deleted.
///
/// Note: `pool.prepare_query()` handles the `?` → `$1` parameter translation for Postgres.
/// This is consistent with other single-parameter queries in this file (e.g., `has_task_for_session`).
/// For multi-parameter queries, some functions in this file use explicit `pool.is_postgres()`
/// branches instead (e.g., `pop`, `pop_by_session`) — see those for the alternative pattern.
pub async fn delete_by_session(pool: &DbPool, session_id: &str) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query("DELETE FROM task_queue WHERE session_id = ?");
    let result = sqlx::query(&query)
        .bind(session_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete_by_session failed: {e}")))?;
    Ok(result.rows_affected() as i64)
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
