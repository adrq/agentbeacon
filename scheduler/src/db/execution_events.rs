use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use sqlx::Row;
use uuid::Uuid;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Execution event entity matching execution_events table schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionEvent {
    pub id: i64,
    pub execution_id: Uuid,
    pub event_type: String, // execution_start|execution_complete|task_start|task_complete|error|info
    pub task_id: Option<String>, // Optional task identifier from workflow.tasks[].id
    pub message: String,
    pub metadata: JsonValue, // JSON object with event metadata
    pub timestamp: DateTime<Utc>,
}

/// Create a new execution event (append-only)
pub async fn create(
    pool: &DbPool,
    execution_id: &Uuid,
    event_type: &str,
    task_id: Option<&str>,
    message: &str,
    metadata: JsonValue,
) -> Result<i64, SchedulerError> {
    // Serialize metadata to JSON string
    let metadata_json = serde_json::to_string(&metadata)
        .map_err(|e| SchedulerError::ValidationFailed(format!("serialize metadata failed: {e}")))?;

    let query = pool.prepare_query(
        r#"
        INSERT INTO execution_events (execution_id, event_type, task_id, message, metadata, timestamp)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        RETURNING id
        "#,
    );

    let row = sqlx::query(&query)
        .bind(execution_id.to_string())
        .bind(event_type)
        .bind(task_id)
        .bind(message)
        .bind(metadata_json)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create execution event failed: {e}")))?;

    let id: i64 = row.get("id");
    Ok(id)
}

/// List execution events for a specific execution (ordered by timestamp)
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn list_by_execution(
    pool: &DbPool,
    execution_id: &Uuid,
) -> Result<Vec<ExecutionEvent>, SchedulerError> {
    let timestamp_fmt = pool.format_timestamp(TimestampColumn::Timestamp);

    let sql = format!(
        r#"
        SELECT id, execution_id, event_type, task_id, message, metadata, {} as timestamp
        FROM execution_events
        WHERE execution_id = ?
        ORDER BY timestamp ASC, id ASC
        "#,
        timestamp_fmt
    );

    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(execution_id.to_string())
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list execution events failed: {e}")))?;

    let events: Result<Vec<ExecutionEvent>, SchedulerError> = rows
        .into_iter()
        .map(|row| {
            let metadata_str: String = row.get("metadata");
            let metadata: JsonValue = serde_json::from_str(&metadata_str)
                .map_err(|e| SchedulerError::Database(format!("Invalid metadata JSON: {e}")))?;

            let timestamp_str: String = row.get("timestamp");

            Ok(ExecutionEvent {
                id: row.get("id"),
                execution_id: Uuid::parse_str(row.get("execution_id")).map_err(|e| {
                    SchedulerError::Database(format!("Invalid execution_id UUID in database: {e}"))
                })?,
                event_type: row.get("event_type"),
                task_id: row.get("task_id"),
                message: row.get("message"),
                metadata,
                timestamp: DateTime::parse_from_rfc3339(&timestamp_str)
                    .map_err(|e| SchedulerError::Database(format!("Invalid timestamp: {e}")))?
                    .with_timezone(&Utc),
            })
        })
        .collect();

    events
}

/// Get recent events across all executions (for monitoring/debugging)
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn list_recent(
    pool: &DbPool,
    limit: Option<i64>,
) -> Result<Vec<ExecutionEvent>, SchedulerError> {
    let limit = limit.unwrap_or(100).min(1000); // Default 100, max 1000

    let timestamp_fmt = pool.format_timestamp(TimestampColumn::Timestamp);

    let sql = format!(
        r#"
        SELECT id, execution_id, event_type, task_id, message, metadata, {} as timestamp
        FROM execution_events
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
        "#,
        timestamp_fmt
    );

    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(limit)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list recent events failed: {e}")))?;

    let events: Result<Vec<ExecutionEvent>, SchedulerError> = rows
        .into_iter()
        .map(|row| {
            let metadata_str: String = row.get("metadata");
            let metadata: JsonValue = serde_json::from_str(&metadata_str).map_err(|e| {
                SchedulerError::Database(format!("deserialize metadata failed: {e}"))
            })?;

            let timestamp_str: String = row.get("timestamp");

            Ok(ExecutionEvent {
                id: row.get("id"),
                execution_id: Uuid::parse_str(row.get("execution_id")).map_err(|e| {
                    SchedulerError::Database(format!("parse execution_id UUID failed: {e}"))
                })?,
                event_type: row.get("event_type"),
                task_id: row.get("task_id"),
                message: row.get("message"),
                metadata,
                timestamp: DateTime::parse_from_rfc3339(&timestamp_str)
                    .map_err(|e| SchedulerError::Database(format!("parse timestamp failed: {e}")))?
                    .with_timezone(&Utc),
            })
        })
        .collect();

    events
}
