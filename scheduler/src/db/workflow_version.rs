use serde::{Deserialize, Serialize};
use sqlx::FromRow;

use crate::db::DbPool;
use crate::error::SchedulerError;

/// WorkflowVersion represents a versioned workflow in the registry
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
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
    pub created_at: String, // Stored as TEXT for SQLx Any compatibility
}

/// Create a new workflow version in the registry
pub async fn create(pool: &DbPool, wf: &WorkflowVersion) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        r#"
        INSERT INTO workflow_version
            (namespace, name, version, is_latest, content_hash, yaml_snapshot,
             git_repo, git_path, git_commit, git_branch, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        "#,
    );
    sqlx::query(&query)
        .bind(&wf.namespace)
        .bind(&wf.name)
        .bind(&wf.version)
        .bind(wf.is_latest)
        .bind(&wf.content_hash)
        .bind(&wf.yaml_snapshot)
        .bind(&wf.git_repo)
        .bind(&wf.git_path)
        .bind(&wf.git_commit)
        .bind(&wf.git_branch)
        .bind(&wf.created_at)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            if e.to_string().contains("UNIQUE") || e.to_string().contains("unique") {
                SchedulerError::Conflict(format!(
                    "Workflow version {}:{}@{} already exists",
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
    let result = if version == "latest" {
        // Resolve :latest to is_latest=true
        let query = pool.prepare_query(
            r#"
            SELECT namespace, name, version, is_latest, content_hash, yaml_snapshot,
                   git_repo, git_path, git_commit, git_branch, created_at
            FROM workflow_version
            WHERE namespace = ? AND name = ? AND is_latest = true
            "#,
        );
        sqlx::query_as::<_, WorkflowVersion>(&query)
            .bind(namespace)
            .bind(name)
            .fetch_optional(pool.as_ref())
            .await
            .map_err(|e| SchedulerError::Database(e.to_string()))?
    } else {
        // Exact version match
        let query = pool.prepare_query(
            r#"
            SELECT namespace, name, version, is_latest, content_hash, yaml_snapshot,
                   git_repo, git_path, git_commit, git_branch, created_at
            FROM workflow_version
            WHERE namespace = ? AND name = ? AND version = ?
            "#,
        );
        sqlx::query_as::<_, WorkflowVersion>(&query)
            .bind(namespace)
            .bind(name)
            .bind(version)
            .fetch_optional(pool.as_ref())
            .await
            .map_err(|e| SchedulerError::Database(e.to_string()))?
    };

    Ok(result)
}

/// List all versions for a workflow (namespace/name)
pub async fn list_versions(
    pool: &DbPool,
    namespace: &str,
    name: &str,
) -> Result<Vec<WorkflowVersion>, SchedulerError> {
    let query = pool.prepare_query(
        r#"
        SELECT namespace, name, version, is_latest, content_hash, yaml_snapshot,
               git_repo, git_path, git_commit, git_branch, created_at
        FROM workflow_version
        WHERE namespace = ? AND name = ?
        ORDER BY created_at DESC
        "#,
    );
    let versions = sqlx::query_as::<_, WorkflowVersion>(&query)
        .bind(namespace)
        .bind(name)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(e.to_string()))?;

    Ok(versions)
}

/// Update is_latest flag for a workflow version
pub async fn update_latest(
    pool: &DbPool,
    namespace: &str,
    name: &str,
    new_latest_version: &str,
) -> Result<(), SchedulerError> {
    // Clear existing is_latest flags
    let query = pool.prepare_query(
        r#"
        UPDATE workflow_version
        SET is_latest = false
        WHERE namespace = ? AND name = ?
        "#,
    );
    sqlx::query(&query)
        .bind(namespace)
        .bind(name)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(e.to_string()))?;

    // Set new is_latest
    let query = pool.prepare_query(
        r#"
        UPDATE workflow_version
        SET is_latest = true
        WHERE namespace = ? AND name = ? AND version = ?
        "#,
    );
    sqlx::query(&query)
        .bind(namespace)
        .bind(name)
        .bind(new_latest_version)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(e.to_string()))?;

    Ok(())
}
