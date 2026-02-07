use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub agent_session_id: Option<String>,
    pub status: String,
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
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(execution_id)
        .bind(parent_session_id)
        .bind(agent_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create session failed: {e}")))?;

    Ok(())
}

#[allow(clippy::uninlined_format_args)]
pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Session, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, status, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE id = ?",
        created_fmt, updated_fmt, completed_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("session not found: {id}"))
            }
            _ => SchedulerError::Database(format!("fetch session failed: {e}")),
        })?;

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
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, status, metadata, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE execution_id = ? ORDER BY created_at ASC",
        created_fmt, updated_fmt, completed_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

fn parse_session_row(row: sqlx::any::AnyRow) -> Result<Session, SchedulerError> {
    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");
    let completed_at_str: Option<String> = row.get("completed_at");

    Ok(Session {
        id: row.get("id"),
        execution_id: row.get("execution_id"),
        parent_session_id: row.get("parent_session_id"),
        agent_id: row.get("agent_id"),
        agent_session_id: row.get("agent_session_id"),
        status: row.get("status"),
        metadata: row.get("metadata"),
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse created_at failed: {e}")))?
            .with_timezone(&Utc),
        updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse updated_at failed: {e}")))?
            .with_timezone(&Utc),
        completed_at: completed_at_str.and_then(|s| {
            DateTime::parse_from_rfc3339(&s)
                .map(|dt| dt.with_timezone(&Utc))
                .ok()
        }),
    })
}
