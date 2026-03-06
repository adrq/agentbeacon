use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_optional_timestamp, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Result of a CAS (Compare-And-Swap) status transition.
#[derive(Debug, PartialEq)]
pub enum CasResult {
    /// Update applied successfully.
    Applied,
    /// Row exists but status didn't match any expected state (concurrent transition won).
    Conflict,
    /// Row does not exist.
    NotFound,
}

/// Execution entity matching new target schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Execution {
    pub id: String,
    pub project_id: Option<String>,
    pub parent_execution_id: Option<String>,
    pub context_id: String,
    pub status: String, // submitted|working|input-required|completed|failed|canceled
    pub title: Option<String>,
    pub input: String,    // plain prompt string
    pub metadata: String, // JSON
    pub max_depth: i64,
    pub max_width: i64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

#[allow(clippy::too_many_arguments)]
pub async fn create(
    pool: &DbPool,
    id: &str,
    context_id: &str,
    input: &str,
    project_id: Option<&str>,
    parent_execution_id: Option<&str>,
    title: Option<&str>,
    max_depth: i64,
    max_width: i64,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        r#"
        INSERT INTO executions (id, project_id, parent_execution_id, context_id, status, title, input, metadata, max_depth, max_width, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'submitted', ?, ?, '{}', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        "#,
    );

    sqlx::query(&query)
        .bind(id)
        .bind(project_id)
        .bind(parent_execution_id)
        .bind(context_id)
        .bind(title)
        .bind(input)
        .bind(max_depth)
        .bind(max_width)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create execution failed: {e}")))?;

    Ok(())
}

/// Get execution by ID
pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Execution, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        r#"
        SELECT id, project_id, parent_execution_id, context_id, status, title, input, metadata,
               max_depth, max_width,
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
        .map_err(|e| map_db_error("execution", id, e))?;

    parse_execution_row(row)
}

/// List executions with optional filters
pub async fn list(
    pool: &DbPool,
    project_id: Option<&str>,
    status: Option<&str>,
    limit: Option<i64>,
    offset: Option<i64>,
) -> Result<Vec<Execution>, SchedulerError> {
    let limit = limit.unwrap_or(50).min(100);
    let offset = offset.unwrap_or(0).max(0);

    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let mut sql = format!(
        "SELECT id, project_id, parent_execution_id, context_id, status, title, input, metadata, max_depth, max_width, {} as created_at, {} as updated_at, {} as completed_at FROM executions WHERE 1=1",
        created_fmt, updated_fmt, completed_fmt
    );

    if project_id.is_some() {
        sql.push_str(" AND project_id = ?");
    }
    if status.is_some() {
        sql.push_str(" AND status = ?");
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT ? OFFSET ?");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    if let Some(pid) = project_id {
        q = q.bind(pid);
    }
    if let Some(st) = status {
        q = q.bind(st);
    }
    q = q.bind(limit);
    q = q.bind(offset);

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list executions failed: {e}")))?;

    rows.into_iter().map(parse_execution_row).collect()
}

/// CAS (Compare-And-Swap) status transition.
///
/// Only transitions from one of the `expected_statuses`. Returns `Applied` on
/// success, `Conflict` if the current status didn't match (another handler won
/// the race), or `NotFound` if the execution row doesn't exist.
///
/// Invariant: executions are never physically deleted during their lifecycle
/// (no DELETE FROM executions in the codebase). This makes the non-atomic
/// UPDATE + EXISTS classification safe — the row cannot disappear between
/// the two queries. The EXISTS check is a defensive assertion, not a
/// race-sensitive classification.
pub async fn update_status_cas(
    pool: &DbPool,
    id: &str,
    new_status: &str,
    expected_statuses: &[&str],
) -> Result<CasResult, SchedulerError> {
    if expected_statuses.is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "update_status_cas: expected_statuses must not be empty".into(),
        ));
    }

    // Build WHERE status IN (?, ?, ...) dynamically
    let placeholders: Vec<&str> = expected_statuses.iter().map(|_| "?").collect();
    let in_clause = placeholders.join(", ");

    let is_terminal = matches!(new_status, "completed" | "failed" | "canceled");
    let sql = if is_terminal {
        format!(
            "UPDATE executions SET status = ?, updated_at = CURRENT_TIMESTAMP, \
             completed_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ({in_clause})"
        )
    } else {
        format!(
            "UPDATE executions SET status = ?, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND status IN ({in_clause})"
        )
    };
    let query = pool.prepare_query(&sql);

    let mut q = sqlx::query(&query).bind(new_status).bind(id);
    for s in expected_statuses {
        q = q.bind(*s);
    }
    let result = q.execute(pool.as_ref()).await.map_err(|e| {
        SchedulerError::Database(format!("CAS update execution status failed: {e}"))
    })?;

    if result.rows_affected() > 0 {
        return Ok(CasResult::Applied);
    }

    // Distinguish conflict (status mismatch) from missing row.
    let exists_sql = pool.prepare_query("SELECT 1 FROM executions WHERE id = ?");
    let exists = sqlx::query(&exists_sql)
        .bind(id)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("CAS existence check failed: {e}")))?;

    if exists.is_some() {
        Ok(CasResult::Conflict)
    } else {
        Ok(CasResult::NotFound)
    }
}

/// Count non-terminal executions for a project
pub async fn count_non_terminal_by_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM executions WHERE project_id = ? AND status NOT IN ('completed', 'failed', 'canceled')",
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

fn parse_execution_row(row: sqlx::any::AnyRow) -> Result<Execution, SchedulerError> {
    Ok(Execution {
        id: row.get("id"),
        project_id: row.get("project_id"),
        parent_execution_id: row.get("parent_execution_id"),
        context_id: row.get("context_id"),
        status: row.get("status"),
        title: row.get("title"),
        input: row.get("input"),
        metadata: row.get("metadata"),
        max_depth: row.get("max_depth"),
        max_width: row.get("max_width"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
        completed_at: parse_optional_timestamp(&row, "completed_at"),
    })
}
