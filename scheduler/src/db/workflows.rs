use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, Row};
use uuid::Uuid;

use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Workflow entity matching workflows table schema
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct Workflow {
    pub id: Uuid,
    pub name: String,
    pub description: Option<String>,
    pub yaml_content: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Create a new workflow
pub async fn create(pool: &DbPool, workflow: &Workflow) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        r#"
        INSERT INTO workflows (id, name, description, yaml_content, created_at, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        "#,
    );

    sqlx::query(&query)
        .bind(workflow.id.to_string())
        .bind(&workflow.name)
        .bind(&workflow.description)
        .bind(&workflow.yaml_content)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create workflow failed: {e}")))?;

    Ok(())
}

/// Get workflow by ID
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn get_by_id(pool: &DbPool, id: &Uuid) -> Result<Workflow, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        r#"
        SELECT id, name, description, yaml_content, {} as created_at, {} as updated_at
        FROM workflows
        WHERE id = ?
        "#,
        created_fmt, updated_fmt
    );

    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id.to_string())
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => SchedulerError::WorkflowNotFound(id.to_string()),
            _ => SchedulerError::Database(format!("fetch workflow failed: {e}")),
        })?;

    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");

    Ok(Workflow {
        id: Uuid::parse_str(row.get("id"))
            .map_err(|e| SchedulerError::Database(format!("parse UUID failed: {e}")))?,
        name: row.get("name"),
        description: row.get("description"),
        yaml_content: row.get("yaml_content"),
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

/// Get workflow by name
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn get_by_name(pool: &DbPool, name: &str) -> Result<Workflow, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        r#"
        SELECT id, name, description, yaml_content, {} as created_at, {} as updated_at
        FROM workflows
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
            sqlx::Error::RowNotFound => SchedulerError::WorkflowNotFound(name.to_string()),
            _ => SchedulerError::Database(format!("fetch workflow failed: {e}")),
        })?;

    let created_at_str: String = row.get("created_at");
    let updated_at_str: String = row.get("updated_at");

    Ok(Workflow {
        id: Uuid::parse_str(row.get("id"))
            .map_err(|e| SchedulerError::Database(format!("parse UUID failed: {e}")))?,
        name: row.get("name"),
        description: row.get("description"),
        yaml_content: row.get("yaml_content"),
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

/// Update workflow by ID
///
/// Parses description from YAML content to ensure database stays synchronized with YAML source.
/// This prevents stale description bugs by always extracting description from the YAML.
pub async fn update(pool: &DbPool, id: &Uuid, yaml_content: &str) -> Result<(), SchedulerError> {
    // Parse YAML to extract description (ensures database stays in sync)
    let parsed: serde_yaml::Value = serde_yaml::from_str(yaml_content)
        .map_err(|e| SchedulerError::ValidationFailed(format!("parse YAML failed: {e}")))?;

    let description = parsed
        .get("description")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    let query = pool.prepare_query(
        r#"
        UPDATE workflows
        SET yaml_content = ?, description = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        "#,
    );

    let result = sqlx::query(&query)
        .bind(yaml_content)
        .bind(description.as_deref())
        .bind(id.to_string())
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update workflow failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::WorkflowNotFound(id.to_string()));
    }

    Ok(())
}

/// List all workflows (optionally filter by name)
#[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
pub async fn list(
    pool: &DbPool,
    name_filter: Option<&str>,
) -> Result<Vec<Workflow>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let rows = if let Some(name) = name_filter {
        let sql = format!(
            r#"
            SELECT id, name, description, yaml_content, {} as created_at, {} as updated_at
            FROM workflows
            WHERE name = ?
            ORDER BY created_at DESC
            "#,
            created_fmt, updated_fmt
        );

        let query = pool.prepare_query(&sql);

        sqlx::query(&query)
            .bind(name)
            .fetch_all(pool.as_ref())
            .await
    } else {
        let sql = format!(
            r#"
            SELECT id, name, description, yaml_content, {} as created_at, {} as updated_at
            FROM workflows
            ORDER BY created_at DESC
            "#,
            created_fmt, updated_fmt
        );

        let query = pool.prepare_query(&sql);

        sqlx::query(&query).fetch_all(pool.as_ref()).await
    }
    .map_err(|e| SchedulerError::Database(format!("list workflows failed: {e}")))?;

    let workflows: Result<Vec<Workflow>, SchedulerError> = rows
        .into_iter()
        .map(|row| {
            let created_at_str: String = row.get("created_at");
            let updated_at_str: String = row.get("updated_at");

            Ok(Workflow {
                id: Uuid::parse_str(row.get("id"))
                    .map_err(|e| SchedulerError::Database(format!("parse UUID failed: {e}")))?,
                name: row.get("name"),
                description: row.get("description"),
                yaml_content: row.get("yaml_content"),
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

    workflows
}

/// Create or update workflow by name (UPSERT semantics)
///
/// Uses atomic database-native upsert to prevent race conditions under concurrent writes.
/// When a workflow with the same name exists, updates yaml_content and description
/// while preserving the original id and created_at timestamp.
pub async fn upsert(pool: &DbPool, workflow: &Workflow) -> Result<(), SchedulerError> {
    let query = if pool.is_postgres() {
        // PostgreSQL: Use ON CONFLICT for atomic upsert
        pool.prepare_query(
            r#"
            INSERT INTO workflows (id, name, description, yaml_content, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                yaml_content = EXCLUDED.yaml_content,
                updated_at = CURRENT_TIMESTAMP
            "#,
        )
    } else {
        // SQLite: Use ON CONFLICT for atomic upsert (SQLite 3.24+)
        pool.prepare_query(
            r#"
            INSERT INTO workflows (id, name, description, yaml_content, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                yaml_content = EXCLUDED.yaml_content,
                updated_at = CURRENT_TIMESTAMP
            "#,
        )
    };

    sqlx::query(&query)
        .bind(workflow.id.to_string())
        .bind(&workflow.name)
        .bind(&workflow.description)
        .bind(&workflow.yaml_content)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("upsert workflow failed: {e}")))?;

    Ok(())
}

/// Delete workflow by ID
///
/// Cascades to related executions and execution_events if foreign keys are enabled.
pub async fn delete(pool: &DbPool, id: &Uuid) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        r#"
        DELETE FROM workflows
        WHERE id = ?
        "#,
    );

    let result = sqlx::query(&query)
        .bind(id.to_string())
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete workflow failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::WorkflowNotFound(id.to_string()));
    }

    Ok(())
}
