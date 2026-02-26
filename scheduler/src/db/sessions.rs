use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_optional_timestamp, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub agent_session_id: Option<String>,
    pub cwd: Option<String>,
    pub status: String,
    pub coordination_mode: String,
    pub metadata: String, // JSON
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    execution_id: &str,
    agent_id: &str,
    parent_session_id: Option<&str>,
    cwd: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, status) VALUES (?, ?, ?, ?, ?, 'submitted')",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(execution_id)
        .bind(parent_session_id)
        .bind(agent_id)
        .bind(cwd)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create session failed: {e}")))?;

    Ok(())
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Session, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, status, coordination_mode, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE id = ?",
        created_fmt, updated_fmt, completed_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("session", id, e))?;

    parse_session_row(row)
}

pub async fn list_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, status, coordination_mode, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE execution_id = ? ORDER BY created_at ASC",
        created_fmt, updated_fmt, completed_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

pub async fn update_status(pool: &DbPool, id: &str, status: &str) -> Result<(), SchedulerError> {
    let is_terminal = matches!(status, "completed" | "failed" | "canceled");

    let query = if is_terminal {
        pool.prepare_query(
            "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        )
    } else {
        pool.prepare_query(
            "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP, completed_at = NULL WHERE id = ?",
        )
    };

    let result = sqlx::query(&query)
        .bind(status)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update session status failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("session not found: {id}")));
    }

    Ok(())
}

pub async fn update_coordination_mode(
    pool: &DbPool,
    id: &str,
    mode: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET coordination_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    );

    let result = sqlx::query(&query)
        .bind(mode)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update coordination_mode failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("session not found: {id}")));
    }

    Ok(())
}

pub async fn list_filtered(
    pool: &DbPool,
    status: Option<&str>,
    execution_id: Option<&str>,
) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let mut sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, status, coordination_mode, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE 1=1",
        created_fmt, updated_fmt, completed_fmt
    );

    if status.is_some() {
        sql.push_str(" AND status = ?");
    }
    if execution_id.is_some() {
        sql.push_str(" AND execution_id = ?");
    }
    sql.push_str(" ORDER BY created_at ASC");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    if let Some(st) = status {
        q = q.bind(st);
    }
    if let Some(eid) = execution_id {
        q = q.bind(eid);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

pub async fn update_agent_session_id(
    pool: &DbPool,
    id: &str,
    agent_session_id: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET agent_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    );

    let result = sqlx::query(&query)
        .bind(agent_session_id)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update agent_session_id failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("session not found: {id}")));
    }

    Ok(())
}

pub async fn find_assignable(pool: &DbPool) -> Result<Option<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, status, coordination_mode, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE coordination_mode = 'sdk' AND status = 'submitted' ORDER BY created_at ASC, id ASC LIMIT 1",
        created_fmt, updated_fmt, completed_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find assignable session failed: {e}")))?;

    match row {
        Some(r) => Ok(Some(parse_session_row(r)?)),
        None => Ok(None),
    }
}

pub async fn claim_assignable(pool: &DbPool, id: &str) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET status = 'working', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'submitted' AND coordination_mode = 'sdk'",
    );

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("claim assignable session failed: {e}")))?;

    Ok(result.rows_affected() > 0)
}

/// Count non-terminal sessions for an agent (used by agent delete guard)
pub async fn count_non_terminal_by_agent(
    pool: &DbPool,
    agent_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM sessions WHERE agent_id = ? AND status NOT IN ('completed', 'failed', 'canceled')",
    );

    let row = sqlx::query(&query)
        .bind(agent_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("count non-terminal sessions failed: {e}"))
        })?;

    Ok(row.get::<i64, _>("cnt"))
}

/// Return all sessions in the subtree rooted at `root_id` (inclusive).
/// Uses a recursive CTE — works on both SQLite and PostgreSQL.
pub async fn get_subtree(pool: &DbPool, root_id: &str) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "WITH RECURSIVE subtree AS (\
            SELECT id FROM sessions WHERE id = ? \
            UNION ALL \
            SELECT s.id FROM sessions s \
            INNER JOIN subtree st ON s.parent_session_id = st.id \
        ) \
        SELECT s.id, s.execution_id, s.parent_session_id, s.agent_id, \
               s.agent_session_id, s.cwd, s.status, s.coordination_mode, \
               s.metadata, {cr} as created_at, {up} as updated_at, \
               {co} as completed_at \
        FROM sessions s \
        INNER JOIN subtree st ON s.id = st.id \
        ORDER BY s.created_at ASC",
        cr = created_fmt,
        up = updated_fmt,
        co = completed_fmt
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(root_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("get subtree failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

fn parse_session_row(row: sqlx::any::AnyRow) -> Result<Session, SchedulerError> {
    Ok(Session {
        id: row.get("id"),
        execution_id: row.get("execution_id"),
        parent_session_id: row.get("parent_session_id"),
        agent_id: row.get("agent_id"),
        agent_session_id: row.get("agent_session_id"),
        cwd: row.get("cwd"),
        status: row.get("status"),
        coordination_mode: row.get("coordination_mode"),
        metadata: row.get("metadata"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
        completed_at: parse_optional_timestamp(&row, "completed_at"),
    })
}
