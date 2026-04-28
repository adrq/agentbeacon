use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_optional_timestamp, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Execution entity.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Execution {
    pub id: String,
    pub project_id: Option<String>,
    pub parent_execution_id: Option<String>,
    pub context_id: String,
    pub desired: String,
    pub outcome: Option<String>,
    pub title: Option<String>,
    pub metadata: String,
    pub max_depth: i64,
    pub max_width: i64,
    pub sandbox_policy: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

/// Build the SELECT column list for execution queries.
fn execution_columns(pool: &DbPool) -> String {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    format!(
        "id, project_id, parent_execution_id, context_id, desired, outcome, title, metadata, \
         max_depth, max_width, sandbox_policy, \
         {created_fmt} as created_at, {updated_fmt} as updated_at, \
         {completed_fmt} as completed_at"
    )
}

#[allow(clippy::too_many_arguments)]
pub async fn create(
    pool: &DbPool,
    id: &str,
    context_id: &str,
    project_id: Option<&str>,
    parent_execution_id: Option<&str>,
    title: Option<&str>,
    max_depth: i64,
    max_width: i64,
    sandbox_policy: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO executions (id, project_id, parent_execution_id, context_id, title, \
         metadata, max_depth, max_width, sandbox_policy, created_at, updated_at) \
         VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(project_id)
        .bind(parent_execution_id)
        .bind(context_id)
        .bind(title)
        .bind(max_depth)
        .bind(max_width)
        .bind(sandbox_policy)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create execution failed: {e}")))?;

    Ok(())
}

/// Get execution by ID
pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Execution, SchedulerError> {
    let cols = execution_columns(pool);
    let sql = format!("SELECT {cols} FROM executions WHERE id = ?");
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("execution", id, e))?;

    parse_execution_row(row)
}

/// List executions with optional filters
pub async fn list(
    pool: &DbPool,
    project_id: Option<&str>,
    limit: Option<i64>,
    offset: Option<i64>,
) -> Result<Vec<Execution>, SchedulerError> {
    let limit = limit.unwrap_or(50).min(100);
    let offset = offset.unwrap_or(0).max(0);

    let cols = execution_columns(pool);
    let mut sql = format!("SELECT {cols} FROM executions WHERE 1=1");

    if project_id.is_some() {
        sql.push_str(" AND project_id = ?");
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT ? OFFSET ?");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    if let Some(pid) = project_id {
        q = q.bind(pid);
    }
    q = q.bind(limit);
    q = q.bind(offset);

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list executions failed: {e}")))?;

    rows.into_iter().map(parse_execution_row).collect()
}

/// List executions by a set of IDs
pub async fn list_by_ids(pool: &DbPool, ids: &[String]) -> Result<Vec<Execution>, SchedulerError> {
    if ids.is_empty() {
        return Ok(vec![]);
    }

    let cols = execution_columns(pool);
    let placeholders: Vec<&str> = ids.iter().map(|_| "?").collect();
    let in_clause = placeholders.join(", ");
    let sql = format!("SELECT {cols} FROM executions WHERE id IN ({in_clause})");
    let prepared = pool.prepare_query(&sql);

    let mut q = sqlx::query(&prepared);
    for id in ids {
        q = q.bind(id);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list executions by ids failed: {e}")))?;

    rows.into_iter().map(parse_execution_row).collect()
}

/// Count non-terminal executions for a project
pub async fn count_non_terminal_by_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM executions WHERE project_id = ? AND outcome IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(project_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("count non-terminal executions failed: {e}"))
        })?;

    Ok(row.get::<i64, _>("cnt"))
}

/// Begin a transaction with the execution-level lock held.
pub async fn begin_execution_tx<'a>(
    pool: &'a DbPool,
    execution_id: &str,
) -> Result<sqlx::Transaction<'a, sqlx::Any>, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin execution tx failed: {e}")))?;
    lock_for_update(pool, &mut tx, execution_id).await?;
    Ok(tx)
}

/// Acquire execution-level lock inside a transaction.
pub async fn lock_for_update(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    id: &str,
) -> Result<(), SchedulerError> {
    let sql = if pool.is_postgres() {
        pool.prepare_query("SELECT id FROM executions WHERE id = ? FOR UPDATE")
    } else {
        pool.prepare_query("SELECT id FROM executions WHERE id = ?")
    };
    sqlx::query(&sql)
        .bind(id)
        .fetch_one(&mut **tx)
        .await
        .map_err(|e| map_db_error("execution lock", id, e))?;
    Ok(())
}

/// Read an execution inside an existing transaction.
/// Use this instead of get_by_id() inside tx blocks to avoid SQLite max_connections=1 deadlock.
pub async fn get_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    id: &str,
) -> Result<Execution, SchedulerError> {
    let cols = execution_columns(pool);
    let sql = format!("SELECT {cols} FROM executions WHERE id = ?");
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(&mut **tx)
        .await
        .map_err(|e| map_db_error("execution", id, e))?;

    parse_execution_row(row)
}

fn parse_execution_row(row: sqlx::any::AnyRow) -> Result<Execution, SchedulerError> {
    Ok(Execution {
        id: row.get("id"),
        project_id: row.get("project_id"),
        parent_execution_id: row.get("parent_execution_id"),
        context_id: row.get("context_id"),
        desired: row.get("desired"),
        outcome: row.get("outcome"),
        title: row.get("title"),
        metadata: row.get("metadata"),
        max_depth: row.get("max_depth"),
        max_width: row.get("max_width"),
        sandbox_policy: row.get("sandbox_policy"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
        completed_at: parse_optional_timestamp(&row, "completed_at"),
    })
}
