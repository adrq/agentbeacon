use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use sqlx::Row;
use uuid::Uuid;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Execution entity matching executions table schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Execution {
    pub id: Uuid,
    pub workflow_id: Uuid,
    pub status: String,         // pending|running|completed|failed|cancelled
    pub task_states: JsonValue, // JSON object mapping task IDs to states
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    // Week 5: Workflow registry metadata
    pub workflow_namespace: Option<String>,
    pub workflow_version: Option<String>,
}

/// Create a new execution
pub async fn create(
    pool: &DbPool,
    workflow_id: &Uuid,
    task_states: JsonValue,
) -> Result<Uuid, SchedulerError> {
    let id = Uuid::new_v4();
    let status = "pending";

    // Serialize task_states to JSON string for storage
    let task_states_json = serde_json::to_string(&task_states)
        .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid task_states JSON: {e}")))?;

    let query = pool.prepare_query(
        r#"
        INSERT INTO executions (id, workflow_id, status, task_states, created_at, updated_at, completed_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
        "#,
    );

    sqlx::query(&query)
        .bind(id.to_string())
        .bind(workflow_id.to_string())
        .bind(status)
        .bind(task_states_json)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to create execution: {e}")))?;

    Ok(id)
}

/// Get execution by ID
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn get_by_id(pool: &DbPool, id: &Uuid) -> Result<Execution, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        r#"
        SELECT id, workflow_id, status, task_states, {} as created_at, {} as updated_at, {} as completed_at,
               workflow_namespace, workflow_version
        FROM executions
        WHERE id = ?
        "#,
        created_fmt, updated_fmt, completed_fmt
    );

    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id.to_string())
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("Execution not found: {id}"))
            }
            _ => SchedulerError::Database(format!("Failed to fetch execution: {e}")),
        })?;

    // Deserialize task_states from JSON string
    let task_states_str: String = row.get("task_states");
    let task_states: JsonValue = serde_json::from_str(&task_states_str).map_err(|e| {
        SchedulerError::Database(format!("Invalid task_states JSON in database: {e}"))
    })?;

    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");
    let completed_at_str: Option<String> = row.get("completed_at");

    Ok(Execution {
        id: Uuid::parse_str(row.get("id"))
            .map_err(|e| SchedulerError::Database(format!("Invalid UUID in database: {e}")))?,
        workflow_id: Uuid::parse_str(row.get("workflow_id")).map_err(|e| {
            SchedulerError::Database(format!("Invalid workflow_id UUID in database: {e}"))
        })?,
        status: row.get("status"),
        task_states,
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| SchedulerError::Database(format!("Invalid created_at timestamp: {e}")))?
            .with_timezone(&Utc),
        updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
            .map_err(|e| SchedulerError::Database(format!("Invalid updated_at timestamp: {e}")))?
            .with_timezone(&Utc),
        completed_at: completed_at_str.and_then(|s| {
            DateTime::parse_from_rfc3339(&s)
                .map(|dt| dt.with_timezone(&Utc))
                .ok()
        }),
        workflow_namespace: row.get("workflow_namespace"),
        workflow_version: row.get("workflow_version"),
    })
}

/// List executions (optionally filter by workflow_id or status)
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn list(
    pool: &DbPool,
    workflow_id: Option<&Uuid>,
    status: Option<&str>,
    limit: Option<i64>,
) -> Result<Vec<Execution>, SchedulerError> {
    let limit = limit.unwrap_or(50).min(100); // Default 50, max 100

    // Format timestamps for cross-database compatibility
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    // Build query dynamically based on filters
    let mut query = format!(
        "SELECT id, workflow_id, status, task_states, {} as created_at, {} as updated_at, {} as completed_at, workflow_namespace, workflow_version FROM executions WHERE 1=1",
        created_fmt, updated_fmt, completed_fmt
    );

    if workflow_id.is_some() {
        query.push_str(" AND workflow_id = ?");
    }
    if status.is_some() {
        query.push_str(" AND status = ?");
    }
    query.push_str(" ORDER BY created_at DESC LIMIT ?");

    let prepared_query = pool.prepare_query(&query);
    let mut sqlx_query = sqlx::query(&prepared_query);

    if let Some(wf_id) = workflow_id {
        sqlx_query = sqlx_query.bind(wf_id.to_string());
    }
    if let Some(st) = status {
        sqlx_query = sqlx_query.bind(st);
    }
    sqlx_query = sqlx_query.bind(limit);

    let rows = sqlx_query
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to list executions: {e}")))?;

    let executions: Result<Vec<Execution>, SchedulerError> = rows
        .into_iter()
        .map(|row| {
            let task_states_str: String = row.get("task_states");
            let task_states: JsonValue = serde_json::from_str(&task_states_str)
                .map_err(|e| SchedulerError::Database(format!("Invalid task_states JSON: {e}")))?;

            let created_at_str: String = row.get("created_at");
            let updated_at_str: String = row.get("updated_at");
            let completed_at_str: Option<String> = row.get("completed_at");

            Ok(Execution {
                id: Uuid::parse_str(row.get("id")).map_err(|e| {
                    SchedulerError::Database(format!("Invalid UUID in database: {e}"))
                })?,
                workflow_id: Uuid::parse_str(row.get("workflow_id")).map_err(|e| {
                    SchedulerError::Database(format!("Invalid workflow_id UUID in database: {e}"))
                })?,
                status: row.get("status"),
                task_states,
                created_at: DateTime::parse_from_rfc3339(&created_at_str)
                    .map_err(|e| {
                        SchedulerError::Database(format!("Invalid created_at timestamp: {e}"))
                    })?
                    .with_timezone(&Utc),
                updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
                    .map_err(|e| {
                        SchedulerError::Database(format!("Invalid updated_at timestamp: {e}"))
                    })?
                    .with_timezone(&Utc),
                completed_at: completed_at_str.and_then(|s| {
                    DateTime::parse_from_rfc3339(&s)
                        .map(|dt| dt.with_timezone(&Utc))
                        .ok()
                }),
                workflow_namespace: row.get("workflow_namespace"),
                workflow_version: row.get("workflow_version"),
            })
        })
        .collect();

    executions
}

/// Update execution status and optionally set completed_at
pub async fn update_status(
    pool: &DbPool,
    id: &Uuid,
    status: &str,
    task_states: &JsonValue,
) -> Result<(), SchedulerError> {
    // Serialize task_states to JSON string
    let task_states_json = serde_json::to_string(task_states)
        .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid task_states JSON: {e}")))?;

    // Determine if this is a terminal state - use CURRENT_TIMESTAMP if so, NULL otherwise
    let is_terminal = matches!(status, "completed" | "failed" | "cancelled");

    let query = if is_terminal {
        pool.prepare_query(
            r#"
            UPDATE executions
            SET status = ?, task_states = ?, updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            "#,
        )
    } else {
        pool.prepare_query(
            r#"
            UPDATE executions
            SET status = ?, task_states = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            "#,
        )
    };

    let result = sqlx::query(&query)
        .bind(status)
        .bind(task_states_json)
        .bind(id.to_string())
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to update execution: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!(
            "Execution not found: {id}"
        )));
    }

    Ok(())
}
