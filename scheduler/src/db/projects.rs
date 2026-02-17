use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub path: String,
    pub default_agent_id: Option<String>,
    pub settings: String, // JSON
    pub deleted_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    path: &str,
    default_agent_id: Option<&str>,
    settings: Option<&str>,
) -> Result<Project, SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO projects (id, name, path, default_agent_id, settings) VALUES (?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(path)
        .bind(default_agent_id)
        .bind(settings.unwrap_or("{}"))
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create project failed: {e}")))?;

    get_by_id(pool, id).await
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Project, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, path, default_agent_id, settings, {} as created_at, {} as updated_at FROM projects WHERE id = ? AND deleted_at IS NULL",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("project", id, e))?;

    parse_project_row(row)
}

pub async fn list(pool: &DbPool) -> Result<Vec<Project>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, path, default_agent_id, settings, {} as created_at, {} as updated_at FROM projects WHERE deleted_at IS NULL ORDER BY created_at DESC",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list projects failed: {e}")))?;

    rows.into_iter().map(parse_project_row).collect()
}

#[allow(clippy::too_many_arguments)]
pub async fn update(
    pool: &DbPool,
    id: &str,
    name: Option<&str>,
    path: Option<&str>,
    default_agent_id: Option<Option<&str>>,
    settings: Option<&str>,
) -> Result<Project, SchedulerError> {
    let mut set_clauses = Vec::new();
    let mut bind_values: Vec<Option<String>> = Vec::new();

    if let Some(v) = name {
        set_clauses.push("name = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }
    if let Some(v) = path {
        set_clauses.push("path = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }
    if let Some(agent_opt) = default_agent_id {
        match agent_opt {
            Some(v) => {
                set_clauses.push("default_agent_id = ?".to_string());
                bind_values.push(Some(v.to_string()));
            }
            None => {
                set_clauses.push("default_agent_id = NULL".to_string());
            }
        }
    }
    if let Some(v) = settings {
        set_clauses.push("settings = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }

    set_clauses.push("updated_at = CURRENT_TIMESTAMP".to_string());

    let sql = format!(
        "UPDATE projects SET {} WHERE id = ? AND deleted_at IS NULL",
        set_clauses.join(", ")
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    for v in bind_values.iter().flatten() {
        q = q.bind(v);
    }
    q = q.bind(id);

    let result = q
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update project failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("project not found: {id}")));
    }

    get_by_id(pool, id).await
}

pub async fn soft_delete(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
    );

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("soft delete project failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("project not found: {id}")));
    }

    Ok(())
}

pub async fn count_by_path(pool: &DbPool, path: &str) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM projects WHERE path = ? AND deleted_at IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(path)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count projects by path failed: {e}")))?;

    Ok(row.get::<i64, _>("cnt"))
}

pub async fn clear_default_agent(pool: &DbPool, agent_id: &str) -> Result<(), SchedulerError> {
    let query = pool
        .prepare_query("UPDATE projects SET default_agent_id = NULL WHERE default_agent_id = ?");

    sqlx::query(&query)
        .bind(agent_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("clear default agent on projects failed: {e}"))
        })?;

    Ok(())
}

fn parse_project_row(row: sqlx::any::AnyRow) -> Result<Project, SchedulerError> {
    Ok(Project {
        id: row.get("id"),
        name: row.get("name"),
        path: row.get("path"),
        default_agent_id: row.get("default_agent_id"),
        settings: row.get("settings"),
        deleted_at: None, // filtered by WHERE deleted_at IS NULL
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}
