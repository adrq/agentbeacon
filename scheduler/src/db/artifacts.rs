use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Artifact {
    pub id: String,
    pub project_id: Option<String>,
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
    project_id: Option<&str>,
    session_id: Option<&str>,
    description: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO artifacts (id, project_id, session_id, artifact_type, name, description, reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(project_id)
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

pub async fn list_by_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<Artifact>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, project_id, session_id, artifact_type, name, description, reference, metadata, {} as created_at FROM artifacts WHERE project_id = ? ORDER BY created_at DESC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list artifacts failed: {e}")))?;

    rows.into_iter().map(parse_artifact_row).collect()
}

fn parse_artifact_row(row: sqlx::any::AnyRow) -> Result<Artifact, SchedulerError> {
    Ok(Artifact {
        id: row.get("id"),
        project_id: row.get("project_id"),
        session_id: row.get("session_id"),
        artifact_type: row.get("artifact_type"),
        name: row.get("name"),
        description: row.get("description"),
        reference: row.get("reference"),
        metadata: row.get("metadata"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}
