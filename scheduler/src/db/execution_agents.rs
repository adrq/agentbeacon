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

use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ExecutionAgentInfo {
    pub agent_id: String,
    pub name: String,
    pub description: Option<String>,
    pub agent_type: String,
}

pub async fn list_agent_configs_for_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<ExecutionAgentInfo>, SchedulerError> {
    let enabled_filter = if pool.is_postgres() {
        "a.enabled = true"
    } else {
        "a.enabled = 1"
    };
    let sql = format!(
        "SELECT a.id as agent_id, a.name, a.description, a.agent_type \
         FROM execution_agents ea \
         JOIN agents a ON ea.agent_id = a.id \
         WHERE ea.execution_id = ? AND a.deleted_at IS NULL AND {enabled_filter} \
         ORDER BY a.name"
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("list agent configs for execution failed: {e}"))
        })?;

    Ok(rows
        .iter()
        .map(|r| ExecutionAgentInfo {
            agent_id: r.get::<String, _>("agent_id"),
            name: r.get::<String, _>("name"),
            description: r.get("description"),
            agent_type: r.get::<String, _>("agent_type"),
        })
        .collect())
}

pub async fn delete(
    pool: &DbPool,
    execution_id: &str,
    agent_id: &str,
) -> Result<bool, SchedulerError> {
    let query =
        pool.prepare_query("DELETE FROM execution_agents WHERE execution_id = ? AND agent_id = ?");

    let result = sqlx::query(&query)
        .bind(execution_id)
        .bind(agent_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete execution_agent failed: {e}")))?;

    Ok(result.rows_affected() > 0)
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
