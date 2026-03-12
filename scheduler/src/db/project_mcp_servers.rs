use serde::Serialize;
use sqlx::Row;

use super::DbPool;
use crate::error::SchedulerError;

#[derive(Debug, Serialize)]
pub struct McpServerPoolEntry {
    pub mcp_server_id: String,
    pub name: String,
    pub transport_type: String,
    pub config: serde_json::Value,
}

pub async fn insert(
    pool: &DbPool,
    project_id: &str,
    mcp_server_id: &str,
) -> Result<(), SchedulerError> {
    let query = if pool.is_postgres() {
        pool.prepare_query(
            "INSERT INTO project_mcp_servers (project_id, mcp_server_id) VALUES (?, ?) ON CONFLICT (project_id, mcp_server_id) DO NOTHING",
        )
    } else {
        pool.prepare_query(
            "INSERT OR IGNORE INTO project_mcp_servers (project_id, mcp_server_id) VALUES (?, ?)",
        )
    };

    sqlx::query(&query)
        .bind(project_id)
        .bind(mcp_server_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert project_mcp_server failed: {e}")))?;

    Ok(())
}

pub async fn delete(
    pool: &DbPool,
    project_id: &str,
    mcp_server_id: &str,
) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query(
        "DELETE FROM project_mcp_servers WHERE project_id = ? AND mcp_server_id = ?",
    );

    let result = sqlx::query(&query)
        .bind(project_id)
        .bind(mcp_server_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete project_mcp_server failed: {e}")))?;

    Ok(result.rows_affected() > 0)
}

pub async fn list_by_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<McpServerPoolEntry>, SchedulerError> {
    let sql = "SELECT m.id as mcp_server_id, m.name, m.transport_type, m.config \
               FROM project_mcp_servers pm \
               JOIN mcp_servers m ON pm.mcp_server_id = m.id \
               WHERE pm.project_id = ? \
               ORDER BY m.name";
    let query = pool.prepare_query(sql);

    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list project MCP servers failed: {e}")))?;

    Ok(rows
        .iter()
        .map(|r| {
            let config_str: String = r.get::<String, _>("config");
            let config =
                serde_json::from_str(&config_str).unwrap_or_else(|_| serde_json::json!({}));
            McpServerPoolEntry {
                mcp_server_id: r.get::<String, _>("mcp_server_id"),
                name: r.get::<String, _>("name"),
                transport_type: r.get::<String, _>("transport_type"),
                config,
            }
        })
        .collect())
}
