use serde::{Deserialize, Serialize};
use sqlx::Row;

use crate::db::DbPool;
use crate::error::SchedulerError;

/// WorkflowVersion represents a versioned workflow in the registry
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowVersion {
    pub namespace: String,
    pub name: String,
    pub version: String,
    pub is_latest: bool,
    pub content_hash: String,
    pub yaml_snapshot: String,
    pub git_repo: Option<String>,
    pub git_path: Option<String>,
    pub git_commit: Option<String>,
    pub git_branch: Option<String>,
    pub created_at: String, // RFC3339 string for SQLx Any compatibility
}

/// Create a new workflow version in the registry
pub async fn create(pool: &DbPool, wf: &WorkflowVersion) -> Result<(), SchedulerError> {
    // Use database-specific SQL for timestamp handling
    // PostgreSQL requires explicit cast from RFC3339 string to TIMESTAMPTZ
    // SQLite accepts string directly for TIMESTAMP columns
    let query = if pool.is_postgres() {
        pool.prepare_query(
            r#"
            INSERT INTO workflow_version
                (namespace, name, version, is_latest, content_hash, yaml_snapshot,
                 git_repo, git_path, git_commit, git_branch, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS TIMESTAMPTZ))
            "#,
        )
    } else {
        pool.prepare_query(
            r#"
            INSERT INTO workflow_version
                (namespace, name, version, is_latest, content_hash, yaml_snapshot,
                 git_repo, git_path, git_commit, git_branch, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            "#,
        )
    };

    // Database-specific binding: PostgreSQL uses bool, SQLite uses i64
    let result = if pool.is_postgres() {
        sqlx::query(&query)
            .bind(&wf.namespace)
            .bind(&wf.name)
            .bind(&wf.version)
            .bind(wf.is_latest) // PostgreSQL: bind bool directly
            .bind(&wf.content_hash)
            .bind(&wf.yaml_snapshot)
            .bind(&wf.git_repo)
            .bind(&wf.git_path)
            .bind(&wf.git_commit)
            .bind(&wf.git_branch)
            .bind(&wf.created_at)
            .execute(pool.as_ref())
            .await
    } else {
        sqlx::query(&query)
            .bind(&wf.namespace)
            .bind(&wf.name)
            .bind(&wf.version)
            .bind(if wf.is_latest { 1i64 } else { 0i64 }) // SQLite: bind i64
            .bind(&wf.content_hash)
            .bind(&wf.yaml_snapshot)
            .bind(&wf.git_repo)
            .bind(&wf.git_path)
            .bind(&wf.git_commit)
            .bind(&wf.git_branch)
            .bind(&wf.created_at)
            .execute(pool.as_ref())
            .await
    };

    result.map_err(|e| {
        if e.to_string().contains("UNIQUE") || e.to_string().contains("unique") {
            SchedulerError::Conflict(format!(
                "workflow version already exists: {}:{}@{}",
                wf.namespace, wf.name, wf.version
            ))
        } else {
            SchedulerError::Database(e.to_string())
        }
    })?;

    Ok(())
}

/// Get workflow version by namespace, name, and version
pub async fn get_by_ref(
    pool: &DbPool,
    namespace: &str,
    name: &str,
    version: &str,
) -> Result<Option<WorkflowVersion>, SchedulerError> {
    let (_query, row) = if version == "latest" {
        // Resolve :latest - database-specific handling
        let created_at_expr = pool.format_timestamp(crate::db::pool::TimestampColumn::CreatedAt);
        let query_str = format!(
            r#"
            SELECT namespace, name, version, is_latest, content_hash, yaml_snapshot,
                   git_repo, git_path, git_commit, git_branch, {created_at_expr} as created_at
            FROM workflow_version
            WHERE namespace = ? AND name = ? AND is_latest = ?
            "#
        );
        let query = pool.prepare_query(&query_str);

        let row = if pool.is_postgres() {
            sqlx::query(&query)
                .bind(namespace)
                .bind(name)
                .bind(true) // PostgreSQL: bind bool
                .fetch_optional(pool.as_ref())
                .await
        } else {
            sqlx::query(&query)
                .bind(namespace)
                .bind(name)
                .bind(1i64) // SQLite: bind i64
                .fetch_optional(pool.as_ref())
                .await
        }
        .map_err(|e| SchedulerError::Database(e.to_string()))?;

        (query, row)
    } else {
        // Exact version match
        let created_at_expr = pool.format_timestamp(crate::db::pool::TimestampColumn::CreatedAt);
        let query_str = format!(
            r#"
            SELECT namespace, name, version, is_latest, content_hash, yaml_snapshot,
                   git_repo, git_path, git_commit, git_branch, {created_at_expr} as created_at
            FROM workflow_version
            WHERE namespace = ? AND name = ? AND version = ?
            "#
        );
        let query = pool.prepare_query(&query_str);

        let row = sqlx::query(&query)
            .bind(namespace)
            .bind(name)
            .bind(version)
            .fetch_optional(pool.as_ref())
            .await
            .map_err(|e| SchedulerError::Database(e.to_string()))?;

        (query, row)
    };

    match row {
        None => Ok(None),
        Some(r) => {
            // Database-specific decoding: PostgreSQL uses bool, SQLite uses i64
            let is_latest = if pool.is_postgres() {
                r.try_get("is_latest")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?
            } else {
                // SQLite: Migration replaces BOOLEAN→INTEGER, so decode as i64
                let val: i64 = r
                    .try_get("is_latest")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?;
                val != 0
            };

            Ok(Some(WorkflowVersion {
                namespace: r
                    .try_get("namespace")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                name: r
                    .try_get("name")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                version: r
                    .try_get("version")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                is_latest,
                content_hash: r
                    .try_get("content_hash")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                yaml_snapshot: r
                    .try_get("yaml_snapshot")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                git_repo: r
                    .try_get("git_repo")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                git_path: r
                    .try_get("git_path")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                git_commit: r
                    .try_get("git_commit")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                git_branch: r
                    .try_get("git_branch")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
                created_at: r
                    .try_get("created_at")
                    .map_err(|e| SchedulerError::Database(e.to_string()))?,
            }))
        }
    }
}

/// List all versions for a workflow (namespace/name)
pub async fn list_versions(
    pool: &DbPool,
    namespace: &str,
    name: &str,
) -> Result<Vec<WorkflowVersion>, SchedulerError> {
    let created_at_expr = pool.format_timestamp(crate::db::pool::TimestampColumn::CreatedAt);
    let query_str = format!(
        r#"
        SELECT namespace, name, version, is_latest, content_hash, yaml_snapshot,
               git_repo, git_path, git_commit, git_branch, {created_at_expr} as created_at
        FROM workflow_version
        WHERE namespace = ? AND name = ?
        ORDER BY created_at DESC
        "#
    );
    let query = pool.prepare_query(&query_str);

    let rows = sqlx::query(&query)
        .bind(namespace)
        .bind(name)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(e.to_string()))?;

    let mut versions = Vec::new();
    for r in rows {
        // Database-specific decoding: PostgreSQL uses bool, SQLite uses i64
        let is_latest = if pool.is_postgres() {
            r.try_get("is_latest")
                .map_err(|e| SchedulerError::Database(e.to_string()))?
        } else {
            // SQLite: Migration replaces BOOLEAN→INTEGER, so decode as i64
            let val: i64 = r
                .try_get("is_latest")
                .map_err(|e| SchedulerError::Database(e.to_string()))?;
            val != 0
        };

        versions.push(WorkflowVersion {
            namespace: r
                .try_get("namespace")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            name: r
                .try_get("name")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            version: r
                .try_get("version")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            is_latest,
            content_hash: r
                .try_get("content_hash")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            yaml_snapshot: r
                .try_get("yaml_snapshot")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            git_repo: r
                .try_get("git_repo")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            git_path: r
                .try_get("git_path")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            git_commit: r
                .try_get("git_commit")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            git_branch: r
                .try_get("git_branch")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
            created_at: r
                .try_get("created_at")
                .map_err(|e| SchedulerError::Database(e.to_string()))?,
        });
    }

    Ok(versions)
}

/// Update is_latest flag for a workflow version
pub async fn update_latest(
    pool: &DbPool,
    namespace: &str,
    name: &str,
    new_latest_version: &str,
) -> Result<(), SchedulerError> {
    // Clear existing is_latest flags - database-specific handling
    let query = pool.prepare_query(
        r#"
        UPDATE workflow_version
        SET is_latest = ?
        WHERE namespace = ? AND name = ?
        "#,
    );

    if pool.is_postgres() {
        sqlx::query(&query)
            .bind(false) // PostgreSQL: bind bool
            .bind(namespace)
            .bind(name)
            .execute(pool.as_ref())
            .await
    } else {
        sqlx::query(&query)
            .bind(0i64) // SQLite: bind i64
            .bind(namespace)
            .bind(name)
            .execute(pool.as_ref())
            .await
    }
    .map_err(|e| SchedulerError::Database(e.to_string()))?;

    // Set new is_latest - database-specific handling
    let query = pool.prepare_query(
        r#"
        UPDATE workflow_version
        SET is_latest = ?
        WHERE namespace = ? AND name = ? AND version = ?
        "#,
    );

    if pool.is_postgres() {
        sqlx::query(&query)
            .bind(true) // PostgreSQL: bind bool
            .bind(namespace)
            .bind(name)
            .bind(new_latest_version)
            .execute(pool.as_ref())
            .await
    } else {
        sqlx::query(&query)
            .bind(1i64) // SQLite: bind i64
            .bind(namespace)
            .bind(name)
            .bind(new_latest_version)
            .execute(pool.as_ref())
            .await
    }
    .map_err(|e| SchedulerError::Database(e.to_string()))?;

    Ok(())
}
