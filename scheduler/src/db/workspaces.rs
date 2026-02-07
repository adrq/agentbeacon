use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Workspace {
    pub id: String,
    pub name: String,
    pub project_path: String,
    pub default_agent_id: Option<String>,
    pub settings: String, // JSON
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    project_path: &str,
    default_agent_id: Option<&str>,
    settings: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO workspaces (id, name, project_path, default_agent_id, settings) VALUES (?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(project_path)
        .bind(default_agent_id)
        .bind(settings.unwrap_or("{}"))
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create workspace failed: {e}")))?;

    Ok(())
}

#[allow(clippy::uninlined_format_args)]
pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Workspace, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, project_path, default_agent_id, settings, {} as created_at, {} as updated_at FROM workspaces WHERE id = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("workspace not found: {id}"))
            }
            _ => SchedulerError::Database(format!("fetch workspace failed: {e}")),
        })?;

    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");

    Ok(Workspace {
        id: row.get("id"),
        name: row.get("name"),
        project_path: row.get("project_path"),
        default_agent_id: row.get("default_agent_id"),
        settings: row.get("settings"),
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse created_at failed: {e}")))?
            .with_timezone(&Utc),
        updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse updated_at failed: {e}")))?
            .with_timezone(&Utc),
    })
}

pub async fn list(pool: &DbPool) -> Result<Vec<Workspace>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, project_path, default_agent_id, settings, {} as created_at, {} as updated_at FROM workspaces ORDER BY created_at DESC",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list workspaces failed: {e}")))?;

    rows.into_iter()
        .map(|row| {
            let created_at_str: String = row.get("created_at");
            let updated_at_str: String = row.get("updated_at");

            Ok(Workspace {
                id: row.get("id"),
                name: row.get("name"),
                project_path: row.get("project_path"),
                default_agent_id: row.get("default_agent_id"),
                settings: row.get("settings"),
                created_at: DateTime::parse_from_rfc3339(&created_at_str)
                    .map_err(|e| SchedulerError::Database(format!("parse created_at failed: {e}")))?
                    .with_timezone(&Utc),
                updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
                    .map_err(|e| SchedulerError::Database(format!("parse updated_at failed: {e}")))?
                    .with_timezone(&Utc),
            })
        })
        .collect()
}
