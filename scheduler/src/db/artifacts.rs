use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Artifact {
    pub id: String,
    pub workspace_id: Option<String>,
    pub session_id: Option<String>,
    pub artifact_type: String, // "file" | "commit" | "url"
    pub name: String,
    pub description: Option<String>,
    pub reference: String, // JSON
    pub metadata: String,  // JSON
    pub created_at: DateTime<Utc>,
}

#[allow(clippy::too_many_arguments)]
pub async fn create(
    pool: &DbPool,
    id: &str,
    artifact_type: &str,
    name: &str,
    reference: &str,
    workspace_id: Option<&str>,
    session_id: Option<&str>,
    description: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO artifacts (id, workspace_id, session_id, artifact_type, name, description, reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(workspace_id)
        .bind(session_id)
        .bind(artifact_type)
        .bind(name)
        .bind(description)
        .bind(reference)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create artifact failed: {e}")))?;

    Ok(())
}

pub async fn list_by_workspace(
    pool: &DbPool,
    workspace_id: &str,
) -> Result<Vec<Artifact>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, workspace_id, session_id, artifact_type, name, description, reference, metadata, {} as created_at FROM artifacts WHERE workspace_id = ? ORDER BY created_at DESC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(workspace_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list artifacts failed: {e}")))?;

    rows.into_iter().map(parse_artifact_row).collect()
}

fn parse_artifact_row(row: sqlx::any::AnyRow) -> Result<Artifact, SchedulerError> {
    let created_at_str: String = row.get("created_at");

    Ok(Artifact {
        id: row.get("id"),
        workspace_id: row.get("workspace_id"),
        session_id: row.get("session_id"),
        artifact_type: row.get("artifact_type"),
        name: row.get("name"),
        description: row.get("description"),
        reference: row.get("reference"),
        metadata: row.get("metadata"),
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse created_at failed: {e}")))?
            .with_timezone(&Utc),
    })
}
