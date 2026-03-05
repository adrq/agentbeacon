use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_bool, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Agent {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub agent_type: String,
    pub driver_id: Option<String>,
    pub config: String,                 // JSON
    pub sandbox_config: Option<String>, // JSON
    pub enabled: bool,
    pub deleted_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[allow(clippy::too_many_arguments)]
pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    agent_type: &str,
    config: &str,
    description: Option<&str>,
    sandbox_config: Option<&str>,
    driver_id: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO agents (id, name, description, agent_type, driver_id, config, sandbox_config) VALUES (?, ?, ?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(description)
        .bind(agent_type)
        .bind(driver_id)
        .bind(config)
        .bind(sandbox_config)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            let err_str = e.to_string();
            if err_str.contains("UNIQUE")
                || err_str.contains("unique")
                || err_str.contains("duplicate key")
            {
                return SchedulerError::Conflict(format!("agent name already exists: {name}"));
            }
            SchedulerError::Database(format!("create agent failed: {e}"))
        })?;

    Ok(())
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Agent, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, description, agent_type, driver_id, config, sandbox_config, enabled, {} as created_at, {} as updated_at FROM agents WHERE id = ? AND deleted_at IS NULL",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("agent", id, e))?;

    parse_agent_row(row)
}

pub async fn get_by_name(pool: &DbPool, name: &str) -> Result<Agent, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, description, agent_type, driver_id, config, sandbox_config, enabled, {} as created_at, {} as updated_at FROM agents WHERE name = ? AND deleted_at IS NULL",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(name)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("agent", name, e))?;

    parse_agent_row(row)
}

pub async fn list(pool: &DbPool) -> Result<Vec<Agent>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, description, agent_type, driver_id, config, sandbox_config, enabled, {} as created_at, {} as updated_at FROM agents WHERE deleted_at IS NULL ORDER BY name",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list agents failed: {e}")))?;

    rows.into_iter().map(parse_agent_row).collect()
}

pub async fn update(
    pool: &DbPool,
    id: &str,
    name: Option<&str>,
    description: Option<Option<&str>>,
    config: Option<&str>,
    sandbox_config: Option<Option<&str>>,
    enabled: Option<bool>,
) -> Result<Agent, SchedulerError> {
    let mut set_clauses = Vec::new();
    let mut bind_values: Vec<BindValue> = Vec::new();

    if let Some(v) = name {
        set_clauses.push("name = ?".to_string());
        bind_values.push(BindValue::Str(v.to_string()));
    }
    if let Some(desc_opt) = description {
        match desc_opt {
            Some(v) => {
                set_clauses.push("description = ?".to_string());
                bind_values.push(BindValue::Str(v.to_string()));
            }
            None => {
                set_clauses.push("description = NULL".to_string());
            }
        }
    }
    if let Some(v) = config {
        set_clauses.push("config = ?".to_string());
        bind_values.push(BindValue::Str(v.to_string()));
    }
    if let Some(sc_opt) = sandbox_config {
        match sc_opt {
            Some(v) => {
                set_clauses.push("sandbox_config = ?".to_string());
                bind_values.push(BindValue::Str(v.to_string()));
            }
            None => {
                set_clauses.push("sandbox_config = NULL".to_string());
            }
        }
    }
    if let Some(v) = enabled {
        set_clauses.push("enabled = ?".to_string());
        bind_values.push(BindValue::Bool(v));
    }

    set_clauses.push("updated_at = CURRENT_TIMESTAMP".to_string());

    let sql = format!(
        "UPDATE agents SET {} WHERE id = ? AND deleted_at IS NULL",
        set_clauses.join(", ")
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    for val in &bind_values {
        match val {
            BindValue::Str(s) => q = q.bind(s),
            BindValue::Bool(b) => q = q.bind(*b),
        }
    }
    q = q.bind(id);

    let result = q.execute(pool.as_ref()).await.map_err(|e| {
        let err_str = e.to_string();
        if err_str.contains("UNIQUE")
            || err_str.contains("unique")
            || err_str.contains("duplicate key")
        {
            if let Some(n) = name {
                return SchedulerError::Conflict(format!("agent name already exists: {n}"));
            }
            return SchedulerError::Conflict("agent name already exists".to_string());
        }
        SchedulerError::Database(format!("update agent failed: {e}"))
    })?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("agent not found: {id}")));
    }

    get_by_id(pool, id).await
}

/// Load agent names by IDs into a HashMap.
/// Uses sequential get_by_id calls — simpler than dynamic IN clause with Any pool,
/// and fine at this scale (typically < 20 agents per execution).
pub async fn get_names_by_ids(
    pool: &DbPool,
    ids: &[String],
) -> Result<std::collections::HashMap<String, String>, SchedulerError> {
    let unique_ids: std::collections::HashSet<&str> = ids.iter().map(|s| s.as_str()).collect();
    let mut map = std::collections::HashMap::new();
    for id in unique_ids {
        match get_by_id(pool, id).await {
            Ok(agent) => {
                map.insert(agent.id, agent.name);
            }
            Err(SchedulerError::NotFound(_)) => {} // deleted agent — skip
            Err(e) => return Err(e),
        }
    }
    Ok(map)
}

pub async fn soft_delete(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE agents SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
    );

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("soft delete agent failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("agent not found: {id}")));
    }

    Ok(())
}

enum BindValue {
    Str(String),
    Bool(bool),
}

fn parse_agent_row(row: sqlx::any::AnyRow) -> Result<Agent, SchedulerError> {
    Ok(Agent {
        id: row.get("id"),
        name: row.get("name"),
        description: row.get("description"),
        agent_type: row.get("agent_type"),
        driver_id: row.get("driver_id"),
        config: row.get("config"),
        sandbox_config: row.get("sandbox_config"),
        enabled: parse_bool(&row, "enabled"),
        deleted_at: None, // filtered by WHERE deleted_at IS NULL
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}
