use chrono::{DateTime, Utc};
use sqlx::Row;

use crate::error::SchedulerError;

/// Parse a required RFC3339 timestamp from a row column.
pub fn parse_timestamp(
    row: &sqlx::any::AnyRow,
    col: &str,
) -> Result<DateTime<Utc>, SchedulerError> {
    let s: String = row.get(col);
    DateTime::parse_from_rfc3339(&s)
        .map(|dt| dt.with_timezone(&Utc))
        .map_err(|e| SchedulerError::Database(format!("parse {col} failed: {e}")))
}

/// Parse an optional RFC3339 timestamp from a row column.
pub fn parse_optional_timestamp(row: &sqlx::any::AnyRow, col: &str) -> Option<DateTime<Utc>> {
    let s: Option<String> = row.get(col);
    s.and_then(|v| {
        DateTime::parse_from_rfc3339(&v)
            .map(|dt| dt.with_timezone(&Utc))
            .ok()
    })
}

/// Parse a boolean that may be stored as INTEGER (SQLite) or native bool (PostgreSQL).
pub fn parse_bool(row: &sqlx::any::AnyRow, col: &str) -> bool {
    row.try_get::<bool, _>(col)
        .unwrap_or_else(|_| row.get::<i32, _>(col) != 0)
}

/// Map a sqlx::Error to a SchedulerError with entity context.
pub fn map_db_error(entity: &str, id: &str, e: sqlx::Error) -> SchedulerError {
    match e {
        sqlx::Error::RowNotFound => SchedulerError::NotFound(format!("{entity} not found: {id}")),
        _ => SchedulerError::Database(format!("fetch {entity} failed: {e}")),
    }
}
