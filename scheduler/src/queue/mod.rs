use serde::{Deserialize, Serialize};
use tokio::sync::Notify;

use crate::db::DbPool;
use crate::error::SchedulerError;

/// TaskAssignment represents a task ready for worker execution (session-based model)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskAssignment {
    pub execution_id: String,
    pub session_id: String,
    pub task_payload: serde_json::Value,
}

/// FIFO task queue backed by the database
///
/// Uses the database as the single source of truth. All operations are transactional,
/// providing ACID guarantees. Performance: 1-5ms per operation is negligible compared
/// to typical task execution times (10-60 seconds for AI agents).
///
/// The `notify` field wakes long-poll waiters (e.g. `next_instruction`) on every push,
/// so they can check their session's inbox without busy-polling.
pub struct TaskQueue {
    db_pool: DbPool,
    notify: Notify,
}

impl TaskQueue {
    pub fn new(db_pool: DbPool) -> Self {
        Self {
            db_pool,
            notify: Notify::new(),
        }
    }

    /// Push task to queue (persisted to database), then wake all waiters
    pub async fn push(&self, task: TaskAssignment) -> Result<(), SchedulerError> {
        crate::db::task_queue::insert(&self.db_pool, &task).await?;
        self.notify.notify_waiters();
        Ok(())
    }

    /// Pop oldest task from queue (FIFO, global)
    pub async fn pop(&self) -> Result<Option<TaskAssignment>, SchedulerError> {
        crate::db::task_queue::pop(&self.db_pool).await
    }

    /// Pop oldest task for a specific session (per-session inbox)
    pub async fn pop_by_session(
        &self,
        session_id: &str,
    ) -> Result<Option<TaskAssignment>, SchedulerError> {
        crate::db::task_queue::pop_by_session(&self.db_pool, session_id).await
    }

    /// Returns a future that completes when the next push occurs.
    /// Callers should call this before checking the queue to avoid race conditions.
    pub fn notified(&self) -> tokio::sync::futures::Notified<'_> {
        self.notify.notified()
    }

    /// Get current queue length
    pub async fn len(&self) -> Result<usize, SchedulerError> {
        crate::db::task_queue::count(&self.db_pool).await
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

    /// Create test execution + session (required by FK constraints)
    async fn create_test_fixtures(pool: &DbPool, execution_id: &str, session_id: &str) {
        sqlx::query(
            "INSERT OR IGNORE INTO agents (id, name, agent_type, config) VALUES ('agent-1', 'test-agent', 'acp', '{}')"
        )
        .execute(pool.as_ref())
        .await
        .expect("Failed to create agent");

        sqlx::query(
            "INSERT INTO executions (id, context_id, status, input) VALUES (?, ?, 'submitted', '{}')"
        )
        .bind(execution_id)
        .bind(execution_id)
        .execute(pool.as_ref())
        .await
        .expect("Failed to create execution");

        sqlx::query(
            "INSERT INTO sessions (id, execution_id, agent_id, status) VALUES (?, ?, 'agent-1', 'submitted')"
        )
        .bind(session_id)
        .bind(execution_id)
        .execute(pool.as_ref())
        .await
        .expect("Failed to create session");
    }

    fn create_test_task(execution_id: &str, session_id: &str) -> TaskAssignment {
        TaskAssignment {
            execution_id: execution_id.to_string(),
            session_id: session_id.to_string(),
            task_payload: serde_json::json!({
                "agent_id": "agent-1",
                "agent_type": "acp",
                "message": {"role": "user", "parts": [{"kind": "text", "text": "Test task"}]}
            }),
        }
    }

    #[tokio::test]
    async fn test_fifo_ordering() {
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_fixtures(pool, "exec-1", "sess-1").await;
        create_test_fixtures(pool, "exec-2", "sess-2").await;
        create_test_fixtures(pool, "exec-3", "sess-3").await;

        queue
            .push(create_test_task("exec-1", "sess-1"))
            .await
            .unwrap();
        queue
            .push(create_test_task("exec-2", "sess-2"))
            .await
            .unwrap();
        queue
            .push(create_test_task("exec-3", "sess-3"))
            .await
            .unwrap();

        let p1 = queue.pop().await.unwrap().unwrap();
        assert_eq!(p1.session_id, "sess-1");

        let p2 = queue.pop().await.unwrap().unwrap();
        assert_eq!(p2.session_id, "sess-2");

        let p3 = queue.pop().await.unwrap().unwrap();
        assert_eq!(p3.session_id, "sess-3");

        assert!(queue.is_empty().await.unwrap());
    }

    #[tokio::test]
    async fn test_empty_queue_pop() {
        let queue = setup_test_queue().await;
        let result = queue.pop().await.unwrap();
        assert!(result.is_none());
    }

    #[tokio::test]
    async fn test_len_and_is_empty() {
        let queue = setup_test_queue().await;
        let pool = &queue.db_pool;

        create_test_fixtures(pool, "exec-1", "sess-1").await;

        assert!(queue.is_empty().await.unwrap());
        assert_eq!(queue.len().await.unwrap(), 0);

        queue
            .push(create_test_task("exec-1", "sess-1"))
            .await
            .unwrap();

        assert!(!queue.is_empty().await.unwrap());
        assert_eq!(queue.len().await.unwrap(), 1);

        queue.pop().await.unwrap();

        assert!(queue.is_empty().await.unwrap());
        assert_eq!(queue.len().await.unwrap(), 0);
    }

    #[tokio::test]
    async fn test_concurrent_workers_no_duplicates() {
        let queue = std::sync::Arc::new(setup_test_queue().await);
        let pool = &queue.db_pool;

        for i in 0..5 {
            create_test_fixtures(pool, &format!("exec-{i}"), &format!("sess-{i}")).await;
            queue
                .push(create_test_task(&format!("exec-{i}"), &format!("sess-{i}")))
                .await
                .unwrap();
        }

        let mut handles = vec![];
        for _ in 0..5 {
            let q = queue.clone();
            handles.push(tokio::spawn(async move { q.pop().await.unwrap() }));
        }

        let mut results = vec![];
        for h in handles {
            if let Some(task) = h.await.unwrap() {
                results.push(task);
            }
        }

        assert_eq!(results.len(), 5);
        let mut ids: Vec<_> = results.iter().map(|t| t.session_id.clone()).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), 5);

        assert!(queue.is_empty().await.unwrap());
    }
}
