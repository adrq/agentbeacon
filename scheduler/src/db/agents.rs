use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Agent {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub agent_type: String,
    pub config: String,                 // JSON
    pub sandbox_config: Option<String>, // JSON
    pub enabled: bool,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    agent_type: &str,
    config: &str,
    description: Option<&str>,
    sandbox_config: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO agents (id, name, description, agent_type, config, sandbox_config) VALUES (?, ?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(description)
        .bind(agent_type)
        .bind(config)
        .bind(sandbox_config)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create agent failed: {e}")))?;

    Ok(())
}

#[allow(clippy::uninlined_format_args)]
pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Agent, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, description, agent_type, config, sandbox_config, enabled, {} as created_at, {} as updated_at FROM agents WHERE id = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => SchedulerError::NotFound(format!("agent not found: {id}")),
            _ => SchedulerError::Database(format!("fetch agent failed: {e}")),
        })?;

    parse_agent_row(row)
}

pub async fn get_by_name(pool: &DbPool, name: &str) -> Result<Agent, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, description, agent_type, config, sandbox_config, enabled, {} as created_at, {} as updated_at FROM agents WHERE name = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(name)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("agent not found: {name}"))
            }
            _ => SchedulerError::Database(format!("fetch agent failed: {e}")),
        })?;

    parse_agent_row(row)
}

pub async fn list(pool: &DbPool) -> Result<Vec<Agent>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, description, agent_type, config, sandbox_config, enabled, {} as created_at, {} as updated_at FROM agents ORDER BY name",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list agents failed: {e}")))?;

    rows.into_iter().map(parse_agent_row).collect()
}

fn parse_agent_row(row: sqlx::any::AnyRow) -> Result<Agent, SchedulerError> {
    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");
    // SQLite stores booleans as integers
    let enabled_int: i32 = row.get("enabled");

    Ok(Agent {
        id: row.get("id"),
        name: row.get("name"),
        description: row.get("description"),
        agent_type: row.get("agent_type"),
        config: row.get("config"),
        sandbox_config: row.get("sandbox_config"),
        enabled: enabled_int != 0,
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse created_at failed: {e}")))?
            .with_timezone(&Utc),
        updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
            .map_err(|e| SchedulerError::Database(format!("parse updated_at failed: {e}")))?
            .with_timezone(&Utc),
    })
}
