use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use crate::error::SchedulerError;

use super::{DbPool, TimestampColumn};

/// Configuration entity matching config table schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub name: String,
    pub value: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Get configuration value by name
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn get(pool: &DbPool, name: &str) -> Result<Config, SchedulerError> {
    // Format timestamps as RFC3339 for cross-database compatibility
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        r#"
        SELECT name, value,
               {} as created_at,
               {} as updated_at
        FROM config
        WHERE name = ?
        "#,
        created_fmt, updated_fmt
    );

    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(name)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("config not found: {name}"))
            }
            _ => SchedulerError::Database(format!("fetch config failed: {e}")),
        })?;

    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");

    Ok(Config {
        name: row.get("name"),
        value: row.get("value"),
        created_at: DateTime::parse_from_rfc3339(&created_at_str)
            .map_err(|e| {
                SchedulerError::Database(format!("parse created_at timestamp failed: {e}"))
            })?
            .with_timezone(&Utc),
        updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
            .map_err(|e| {
                SchedulerError::Database(format!("parse updated_at timestamp failed: {e}"))
            })?
            .with_timezone(&Utc),
    })
}

/// List all configuration entries
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn list(pool: &DbPool) -> Result<Vec<Config>, SchedulerError> {
    // Format timestamps as RFC3339 for cross-database compatibility
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        r#"
        SELECT name, value,
               {} as created_at,
               {} as updated_at
        FROM config
        ORDER BY name
        "#,
        created_fmt, updated_fmt
    );

    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list config failed: {e}")))?;

    let configs: Result<Vec<Config>, SchedulerError> = rows
        .into_iter()
        .map(|row| {
            let created_at_str: String = row.get("created_at");
            let updated_at_str: String = row.get("updated_at");

            Ok(Config {
                name: row.get("name"),
                value: row.get("value"),
                created_at: DateTime::parse_from_rfc3339(&created_at_str)
                    .map_err(|e| {
                        SchedulerError::Database(format!("parse created_at timestamp failed: {e}"))
                    })?
                    .with_timezone(&Utc),
                updated_at: DateTime::parse_from_rfc3339(&updated_at_str)
                    .map_err(|e| {
                        SchedulerError::Database(format!("parse updated_at timestamp failed: {e}"))
                    })?
                    .with_timezone(&Utc),
            })
        })
        .collect();

    configs
}

/// UPSERT configuration entry (atomic, database-native operation)
///
/// Uses single-statement upsert to prevent race conditions under concurrent writes:
/// - PostgreSQL: INSERT ... ON CONFLICT (name) DO UPDATE
/// - SQLite: INSERT OR REPLACE INTO ...
///
/// Both approaches are atomic and safe for concurrent access.
pub async fn upsert(pool: &DbPool, name: &str, value: &str) -> Result<(), SchedulerError> {
    let query = if pool.is_postgres() {
        // PostgreSQL: Use ON CONFLICT for atomic upsert
        pool.prepare_query(
            r#"
            INSERT INTO config (name, value, created_at, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (name) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = CURRENT_TIMESTAMP
            "#,
        )
    } else {
        // SQLite: Use INSERT OR REPLACE for atomic upsert
        // Note: This preserves created_at because it's not in the VALUES clause
        pool.prepare_query(
            r#"
            INSERT INTO config (name, value, created_at, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (name) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = CURRENT_TIMESTAMP
            "#,
        )
    };

    sqlx::query(&query)
        .bind(name)
        .bind(value)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("upsert config failed: {e}")))?;

    Ok(())
}
