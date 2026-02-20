use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub id: i64,
    pub execution_id: String,
    pub session_id: Option<String>, // nullable for execution-level events
    pub event_type: String,         // "message" | "state_change"
    pub payload: String,            // JSON
    pub created_at: DateTime<Utc>,
}

pub async fn insert(
    pool: &DbPool,
    execution_id: &str,
    session_id: Option<&str>,
    event_type: &str,
    payload: &str,
) -> Result<i64, SchedulerError> {
    let sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, ?, ?) RETURNING id",
    );

    let row = sqlx::query(&sql)
        .bind(execution_id)
        .bind(session_id)
        .bind(event_type)
        .bind(payload)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert event failed: {e}")))?;

    let id: i64 = row
        .try_get("id")
        .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?;
    Ok(id)
}

pub async fn list_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, execution_id, session_id, event_type, payload, {} as created_at FROM events WHERE execution_id = ? ORDER BY id ASC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events failed: {e}")))?;

    rows.into_iter().map(parse_event_row).collect()
}

pub async fn list_by_session(
    pool: &DbPool,
    session_id: &str,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, execution_id, session_id, event_type, payload, {} as created_at FROM events WHERE session_id = ? ORDER BY id ASC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(session_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events failed: {e}")))?;

    rows.into_iter().map(parse_event_row).collect()
}

pub async fn list_by_execution_since(
    pool: &DbPool,
    execution_id: &str,
    since_id: i64,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, execution_id, session_id, event_type, payload, {} as created_at \
         FROM events WHERE execution_id = ? AND id > ? ORDER BY id ASC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .bind(since_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events since failed: {e}")))?;

    rows.into_iter().map(parse_event_row).collect()
}

fn parse_event_row(row: sqlx::any::AnyRow) -> Result<Event, SchedulerError> {
    Ok(Event {
        id: row.get("id"),
        execution_id: row.get("execution_id"),
        session_id: row.get("session_id"),
        event_type: row.get("event_type"),
        payload: row.get("payload"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}
