use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Execution entity matching new target schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Execution {
    pub id: String,
    pub workspace_id: Option<String>,
    pub parent_execution_id: Option<String>,
    pub context_id: String,
    pub status: String, // submitted|working|input-required|completed|failed|canceled
    pub title: Option<String>,
    pub input: String,    // JSON (A2A Message)
    pub metadata: String, // JSON
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

/// Create a new execution
pub async fn create(
    pool: &DbPool,
    id: &str,
    context_id: &str,
    input: &str,
    workspace_id: Option<&str>,
    parent_execution_id: Option<&str>,
    title: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        r#"
        INSERT INTO executions (id, workspace_id, parent_execution_id, context_id, status, title, input, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'submitted', ?, ?, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        "#,
    );

    sqlx::query(&query)
        .bind(id)
        .bind(workspace_id)
        .bind(parent_execution_id)
        .bind(context_id)
        .bind(title)
        .bind(input)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create execution failed: {e}")))?;

    Ok(())
}

/// Get execution by ID
#[allow(clippy::uninlined_format_args)]
pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Execution, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        r#"
        SELECT id, workspace_id, parent_execution_id, context_id, status, title, input, metadata,
               {} as created_at, {} as updated_at, {} as completed_at
        FROM executions
        WHERE id = ?
        "#,
        created_fmt, updated_fmt, completed_fmt
    );

    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("execution not found: {id}"))
            }
            _ => SchedulerError::Database(format!("fetch execution failed: {e}")),
        })?;

    parse_execution_row(row)
}

/// List executions with optional filters
#[allow(clippy::uninlined_format_args)]
pub async fn list(
    pool: &DbPool,
    workspace_id: Option<&str>,
    status: Option<&str>,
    limit: Option<i64>,
) -> Result<Vec<Execution>, SchedulerError> {
    let limit = limit.unwrap_or(50).min(100);

    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let mut sql = format!(
        "SELECT id, workspace_id, parent_execution_id, context_id, status, title, input, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM executions WHERE 1=1",
        created_fmt, updated_fmt, completed_fmt
    );

    if workspace_id.is_some() {
        sql.push_str(" AND workspace_id = ?");
    }
    if status.is_some() {
        sql.push_str(" AND status = ?");
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT ?");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    if let Some(ws_id) = workspace_id {
        q = q.bind(ws_id);
    }
    if let Some(st) = status {
        q = q.bind(st);
    }
    q = q.bind(limit);

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list executions failed: {e}")))?;

    rows.into_iter().map(parse_execution_row).collect()
}

/// Update execution status
pub async fn update_status(pool: &DbPool, id: &str, status: &str) -> Result<(), SchedulerError> {
    let is_terminal = matches!(status, "completed" | "failed" | "canceled");

    let query = if is_terminal {
        pool.prepare_query(
            "UPDATE executions SET status = ?, updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        )
    } else {
        pool.prepare_query(
            "UPDATE executions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        )
    };

    let result = sqlx::query(&query)
        .bind(status)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update execution status failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!(
            "execution not found: {id}"
        )));
    }

    Ok(())
}

fn parse_execution_row(row: sqlx::any::AnyRow) -> Result<Execution, SchedulerError> {
    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");
    let completed_at_str: Option<String> = row.get("completed_at");

    Ok(Execution {
        id: row.get("id"),
        workspace_id: row.get("workspace_id"),
        parent_execution_id: row.get("parent_execution_id"),
        context_id: row.get("context_id"),
        status: row.get("status"),
        title: row.get("title"),
        input: row.get("input"),
        metadata: row.get("metadata"),
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| {
                SchedulerError::Database(format!("parse created_at timestamp failed: {e}"))
            })?
            .with_timezone(&Utc),
        updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
            .map_err(|e| {
                SchedulerError::Database(format!("parse updated_at timestamp failed: {e}"))
            })?
            .with_timezone(&Utc),
        completed_at: completed_at_str.and_then(|s| {
            DateTime::parse_from_rfc3339(&s)
                .map(|dt| dt.with_timezone(&Utc))
                .ok()
        }),
    })
}
