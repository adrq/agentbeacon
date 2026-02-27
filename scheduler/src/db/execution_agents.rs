use sqlx::Row;

use super::DbPool;
use crate::error::SchedulerError;

pub async fn insert(
    pool: &DbPool,
    execution_id: &str,
    agent_id: &str,
) -> Result<(), SchedulerError> {
    let query = if pool.is_postgres() {
        pool.prepare_query(
            "INSERT INTO execution_agents (execution_id, agent_id) VALUES (?, ?) ON CONFLICT (execution_id, agent_id) DO NOTHING",
        )
    } else {
        pool.prepare_query(
            "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
        )
    };

    sqlx::query(&query)
        .bind(execution_id)
        .bind(agent_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert execution_agent failed: {e}")))?;

    Ok(())
}

pub async fn insert_batch(
    pool: &DbPool,
    execution_id: &str,
    agent_ids: &[&str],
) -> Result<(), SchedulerError> {
    for agent_id in agent_ids {
        insert(pool, execution_id, agent_id).await?;
    }
    Ok(())
}

pub async fn list_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<String>, SchedulerError> {
    let query = pool.prepare_query("SELECT agent_id FROM execution_agents WHERE execution_id = ?");

    let rows = sqlx::query(&query)
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list execution_agents failed: {e}")))?;

    Ok(rows
        .iter()
        .map(|r| r.get::<String, _>("agent_id"))
        .collect())
}
