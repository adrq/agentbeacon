use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Driver {
    pub id: String,
    pub name: String,
    pub platform: String,
    pub config: String, // JSON
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    platform: &str,
    config: &str,
) -> Result<(), SchedulerError> {
    let query =
        pool.prepare_query("INSERT INTO drivers (id, name, platform, config) VALUES (?, ?, ?, ?)");

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(platform)
        .bind(config)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            let err_str = e.to_string();
            if err_str.contains("UNIQUE")
                || err_str.contains("unique")
                || err_str.contains("duplicate key")
            {
                return SchedulerError::Conflict(format!("driver name already exists: {name}"));
            }
            SchedulerError::Database(format!("create driver failed: {e}"))
        })?;

    Ok(())
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Driver, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, platform, config, {} as created_at, {} as updated_at FROM drivers WHERE id = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("driver", id, e))?;

    parse_driver_row(row)
}

pub async fn get_by_name(pool: &DbPool, name: &str) -> Result<Driver, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, platform, config, {} as created_at, {} as updated_at FROM drivers WHERE name = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(name)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("driver", name, e))?;

    parse_driver_row(row)
}

pub async fn get_by_platform(pool: &DbPool, platform: &str) -> Result<Driver, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, platform, config, {} as created_at, {} as updated_at FROM drivers WHERE platform = ?",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(platform)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("driver", platform, e))?;

    parse_driver_row(row)
}

pub async fn list(pool: &DbPool) -> Result<Vec<Driver>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, platform, config, {} as created_at, {} as updated_at FROM drivers ORDER BY name",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list drivers failed: {e}")))?;

    rows.into_iter().map(parse_driver_row).collect()
}

pub async fn update(
    pool: &DbPool,
    id: &str,
    name: Option<&str>,
    config: Option<&str>,
) -> Result<Driver, SchedulerError> {
    let mut set_clauses = Vec::new();
    let mut bind_values: Vec<String> = Vec::new();

    if let Some(v) = name {
        set_clauses.push("name = ?".to_string());
        bind_values.push(v.to_string());
    }
    if let Some(v) = config {
        set_clauses.push("config = ?".to_string());
        bind_values.push(v.to_string());
    }

    if set_clauses.is_empty() {
        return get_by_id(pool, id).await;
    }

    set_clauses.push("updated_at = CURRENT_TIMESTAMP".to_string());

    let sql = format!("UPDATE drivers SET {} WHERE id = ?", set_clauses.join(", "));
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    for val in &bind_values {
        q = q.bind(val);
    }
    q = q.bind(id);

    let result = q.execute(pool.as_ref()).await.map_err(|e| {
        let err_str = e.to_string();
        if err_str.contains("UNIQUE")
            || err_str.contains("unique")
            || err_str.contains("duplicate key")
        {
            if let Some(n) = name {
                return SchedulerError::Conflict(format!("driver name already exists: {n}"));
            }
            return SchedulerError::Conflict("driver name already exists".to_string());
        }
        SchedulerError::Database(format!("update driver failed: {e}"))
    })?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("driver not found: {id}")));
    }

    get_by_id(pool, id).await
}

pub async fn hard_delete(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query("DELETE FROM drivers WHERE id = ?");

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete driver failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("driver not found: {id}")));
    }

    Ok(())
}

pub async fn count_agents_by_driver(pool: &DbPool, driver_id: &str) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query("SELECT COUNT(*) as cnt FROM agents WHERE driver_id = ?");

    let row = sqlx::query(&query)
        .bind(driver_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count agents by driver failed: {e}")))?;

    Ok(row.get::<i64, _>("cnt"))
}

fn parse_driver_row(row: sqlx::any::AnyRow) -> Result<Driver, SchedulerError> {
    Ok(Driver {
        id: row.get("id"),
        name: row.get("name"),
        platform: row.get("platform"),
        config: row.get("config"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}
