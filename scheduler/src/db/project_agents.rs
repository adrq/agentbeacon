use serde::Serialize;
use sqlx::Row;

use super::DbPool;
use crate::error::SchedulerError;

#[derive(Debug, Serialize)]
pub struct AgentPoolEntry {
    pub agent_id: String,
    pub name: String,
    pub description: Option<String>,
    pub agent_type: String,
}

pub async fn insert(pool: &DbPool, project_id: &str, agent_id: &str) -> Result<(), SchedulerError> {
    let query = if pool.is_postgres() {
        pool.prepare_query(
            "INSERT INTO project_agents (project_id, agent_id) VALUES (?, ?) ON CONFLICT (project_id, agent_id) DO NOTHING",
        )
    } else {
        pool.prepare_query(
            "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
        )
    };

    sqlx::query(&query)
        .bind(project_id)
        .bind(agent_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert project_agent failed: {e}")))?;

    Ok(())
}

pub async fn delete(
    pool: &DbPool,
    project_id: &str,
    agent_id: &str,
) -> Result<bool, SchedulerError> {
    let query =
        pool.prepare_query("DELETE FROM project_agents WHERE project_id = ? AND agent_id = ?");

    let result = sqlx::query(&query)
        .bind(project_id)
        .bind(agent_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete project_agent failed: {e}")))?;

    Ok(result.rows_affected() > 0)
}

pub async fn list_by_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<AgentPoolEntry>, SchedulerError> {
    let enabled_filter = if pool.is_postgres() {
        "a.enabled = true"
    } else {
        "a.enabled = 1"
    };
    let sql = format!(
        "SELECT a.id as agent_id, a.name, a.description, a.agent_type \
         FROM project_agents pa \
         JOIN agents a ON pa.agent_id = a.id \
         WHERE pa.project_id = ? AND a.deleted_at IS NULL AND {enabled_filter} \
         ORDER BY a.name"
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list project agents failed: {e}")))?;

    Ok(rows
        .iter()
        .map(|r| AgentPoolEntry {
            agent_id: r.get::<String, _>("agent_id"),
            name: r.get::<String, _>("name"),
            description: r.get("description"),
            agent_type: r.get::<String, _>("agent_type"),
        })
        .collect())
}
