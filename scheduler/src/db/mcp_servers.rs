use chrono::{DateTime, Utc};
use sqlx::Row;

use super::helpers::{map_db_error, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone)]
pub struct McpServer {
    pub id: String,
    pub name: String,
    pub transport_type: String,
    pub config: serde_json::Value,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    transport_type: &str,
    config: &str,
) -> Result<McpServer, SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO mcp_servers (id, name, transport_type, config) VALUES (?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(transport_type)
        .bind(config)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            let err_str = e.to_string();
            if err_str.contains("UNIQUE")
                || err_str.contains("unique")
                || err_str.contains("duplicate key")
            {
                return SchedulerError::Conflict(format!("MCP server name already exists: {name}"));
            }
            SchedulerError::Database(format!("create MCP server failed: {e}"))
        })?;

    get_by_id(pool, id).await
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<McpServer, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, transport_type, config, {} as created_at, {} as updated_at FROM mcp_servers WHERE id = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("MCP server", id, e))?;

    parse_row(row)
}

pub async fn list(pool: &DbPool) -> Result<Vec<McpServer>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, transport_type, config, {} as created_at, {} as updated_at FROM mcp_servers ORDER BY name",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list MCP servers failed: {e}")))?;

    rows.into_iter().map(parse_row).collect()
}

pub async fn update(
    pool: &DbPool,
    id: &str,
    name: Option<&str>,
    transport_type: Option<&str>,
    config: Option<&str>,
) -> Result<McpServer, SchedulerError> {
    let mut set_clauses = Vec::new();
    let mut bind_values: Vec<String> = Vec::new();

    if let Some(v) = name {
        set_clauses.push("name = ?".to_string());
        bind_values.push(v.to_string());
    }
    if let Some(v) = transport_type {
        set_clauses.push("transport_type = ?".to_string());
        bind_values.push(v.to_string());
    }
    if let Some(v) = config {
        set_clauses.push("config = ?".to_string());
        bind_values.push(v.to_string());
    }

    set_clauses.push("updated_at = CURRENT_TIMESTAMP".to_string());

    let sql = format!(
        "UPDATE mcp_servers SET {} WHERE id = ?",
        set_clauses.join(", ")
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    for val in &bind_values {
        q = q.bind(val);
    }
    q = q.bind(id);

    let result = q.execute(pool.as_ref()).await.map_err(|e| {
        let err_str = e.to_string();
        if err_str.contains("UNIQUE")
            || err_str.contains("unique")
            || err_str.contains("duplicate key")
        {
            if let Some(n) = name {
                return SchedulerError::Conflict(format!("MCP server name already exists: {n}"));
            }
            return SchedulerError::Conflict("MCP server name already exists".to_string());
        }
        SchedulerError::Database(format!("update MCP server failed: {e}"))
    })?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!(
            "MCP server not found: {id}"
        )));
    }

    get_by_id(pool, id).await
}

pub async fn delete(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query("DELETE FROM mcp_servers WHERE id = ?");

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete MCP server failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!(
            "MCP server not found: {id}"
        )));
    }

    Ok(())
}

/// Remove junction rows for soft-deleted projects that have no active executions.
/// Projects with non-terminal executions are NOT cleaned up — those executions
/// still need MCP servers for delegate and recovery payloads.
pub async fn cleanup_orphaned_attachments(
    pool: &DbPool,
    mcp_server_id: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "DELETE FROM project_mcp_servers \
         WHERE mcp_server_id = ? AND project_id IN (\
           SELECT id FROM projects WHERE deleted_at IS NOT NULL \
           AND id NOT IN (\
             SELECT DISTINCT project_id FROM executions \
             WHERE project_id IS NOT NULL \
             AND status NOT IN ('completed', 'failed', 'canceled')\
           )\
         )",
    );

    sqlx::query(&query)
        .bind(mcp_server_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("cleanup orphaned attachments failed: {e}"))
        })?;

    Ok(())
}

/// Count junction rows on soft-deleted projects that still have active executions.
pub async fn count_active_deleted_project_attachments(
    pool: &DbPool,
    mcp_server_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM project_mcp_servers pms \
         JOIN projects p ON p.id = pms.project_id \
         WHERE pms.mcp_server_id = ? AND p.deleted_at IS NOT NULL \
         AND EXISTS (\
           SELECT 1 FROM executions e \
           WHERE e.project_id = p.id \
           AND e.status NOT IN ('completed', 'failed', 'canceled')\
         )",
    );

    let row = sqlx::query(&query)
        .bind(mcp_server_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!(
                "count active deleted project attachments failed: {e}"
            ))
        })?;

    Ok(row.try_get("cnt").unwrap_or(0))
}

/// Count how many projects reference this MCP server.
pub async fn count_project_attachments(
    pool: &DbPool,
    mcp_server_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM project_mcp_servers \
         JOIN projects ON projects.id = project_mcp_servers.project_id \
         WHERE project_mcp_servers.mcp_server_id = ? AND projects.deleted_at IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(mcp_server_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count project attachments failed: {e}")))?;

    let count: i64 = row.try_get("cnt").unwrap_or(0);
    Ok(count)
}

fn parse_row(row: sqlx::any::AnyRow) -> Result<McpServer, SchedulerError> {
    let config_str: String = row.get("config");
    let config = serde_json::from_str(&config_str).unwrap_or_else(|_| serde_json::json!({}));

    Ok(McpServer {
        id: row.get("id"),
        name: row.get("name"),
        transport_type: row.get("transport_type"),
        config,
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}
