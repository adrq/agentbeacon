// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

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

/// A database transaction scoped to a single execution.
pub struct ExecutionTx<'a> {
    tx: sqlx::Transaction<'a, sqlx::Any>,
    execution_id: String,
    verified_sessions: std::collections::HashSet<String>,
}

impl<'a> ExecutionTx<'a> {
    /// The execution this transaction is scoped to.
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }

    /// Commit the transaction.
    pub async fn commit(self) -> Result<(), sqlx::Error> {
        self.tx.commit().await
    }

    /// Roll the transaction back.
    pub async fn rollback(self) -> Result<(), sqlx::Error> {
        self.tx.rollback().await
    }

    /// Whether this transaction already checked that `session_id` belongs to
    /// its execution.
    pub fn session_verified(&self, session_id: &str) -> bool {
        self.verified_sessions.contains(session_id)
    }

    /// Record that `session_id` was checked against this transaction's execution.
    pub fn mark_session_verified(&mut self, session_id: &str) {
        self.verified_sessions.insert(session_id.to_string());
    }
}

impl<'a> std::ops::Deref for ExecutionTx<'a> {
    type Target = sqlx::Transaction<'a, sqlx::Any>;

    fn deref(&self) -> &Self::Target {
        &self.tx
    }
}

impl std::ops::DerefMut for ExecutionTx<'_> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.tx
    }
}

/// Begin a transaction with the execution-level lock held.
pub async fn begin_execution_tx<'a>(
    pool: &'a DbPool,
    execution_id: &str,
) -> Result<ExecutionTx<'a>, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin execution tx failed: {e}")))?;
    lock_for_update(pool, &mut tx, execution_id).await?;
    Ok(ExecutionTx {
        tx,
        execution_id: execution_id.to_string(),
        verified_sessions: std::collections::HashSet::new(),
    })
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

/// List IDs of active (non-terminal) executions.
pub async fn list_active_ids(pool: &DbPool) -> Result<Vec<String>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT id FROM executions WHERE outcome IS NULL AND desired != 'terminate'",
    );
    let rows = sqlx::query(&sql)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list active execution ids failed: {e}")))?;
    Ok(rows.iter().map(|r| r.get("id")).collect())
}

/// One active execution's decision-visible state.
pub struct ActiveExecution {
    pub id: String,
    pub outcome: Option<String>,
    pub desired: String,
    pub title: Option<String>,
}

/// One active execution with its platform-lane high-water mark.
pub struct ActiveExecutionMark {
    pub id: String,
    pub outcome: Option<String>,
    pub desired: String,
    pub title: Option<String>,
    pub platform_max: Option<i64>,
}

/// List the executions that can hold actionable decisions, each with its own
/// platform-lane high-water mark.
pub async fn list_active_with_platform_mark(
    pool: &DbPool,
) -> Result<Vec<ActiveExecutionMark>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT e.id, e.outcome, e.desired, e.title,          (SELECT MAX(ev.id) FROM events ev           WHERE ev.execution_id = e.id AND ev.event_type = 'platform') AS platform_max          FROM executions e          WHERE e.outcome IS NULL AND e.desired != 'terminate' ORDER BY e.id ASC",
    );
    let rows = sqlx::query(&sql)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list active executions failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| ActiveExecutionMark {
            id: r.get("id"),
            outcome: r.get("outcome"),
            desired: r.get("desired"),
            title: r.get("title"),
            platform_max: r.try_get("platform_max").unwrap_or(None),
        })
        .collect())
}

/// List the executions that can hold actionable decisions.
pub async fn list_active(pool: &DbPool) -> Result<Vec<ActiveExecution>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT id, outcome, desired, title FROM executions \
         WHERE outcome IS NULL AND desired != 'terminate' ORDER BY id ASC",
    );
    let rows = sqlx::query(&sql)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list active executions failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| ActiveExecution {
            id: r.get("id"),
            outcome: r.get("outcome"),
            desired: r.get("desired"),
            title: r.get("title"),
        })
        .collect())
}

/// Titles of the given executions, keyed by id.
pub async fn titles_for(
    pool: &DbPool,
    ids: &[String],
) -> Result<std::collections::HashMap<String, Option<String>>, SchedulerError> {
    if ids.is_empty() {
        return Ok(std::collections::HashMap::new());
    }
    let placeholders = ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
    let sql = pool.prepare_query(&format!(
        "SELECT id, title FROM executions WHERE id IN ({placeholders})"
    ));
    let mut query = sqlx::query(&sql);
    for id in ids {
        query = query.bind(id);
    }
    let rows = query
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list execution titles failed: {e}")))?;
    Ok(rows.iter().map(|r| (r.get("id"), r.get("title"))).collect())
}

/// List IDs of the most recent terminal executions.
pub async fn list_recent_terminal_ids(
    pool: &DbPool,
    limit: i64,
) -> Result<Vec<String>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT id FROM executions WHERE outcome IS NOT NULL OR desired = 'terminate' \
         ORDER BY created_at DESC, id DESC LIMIT ?",
    );
    let rows = sqlx::query(&sql)
        .bind(limit)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("list recent terminal execution ids failed: {e}"))
        })?;
    Ok(rows.iter().map(|r| r.get("id")).collect())
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
