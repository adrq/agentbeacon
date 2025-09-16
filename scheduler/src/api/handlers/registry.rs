use axum::{
    Json,
    extract::{Path, State},
    http::StatusCode,
};
use chrono::Utc;
use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::app::AppState;
use crate::db::{WorkflowVersion, workflow_version};
use crate::error::SchedulerError;
use crate::registry::calculate_content_hash;

/// Register workflow request
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RegisterWorkflowRequest {
    pub namespace: String,
    pub name: String,
    pub version: String,
    #[serde(default)]
    pub is_latest: bool,
    pub workflow_yaml: String,
}

/// Register workflow response
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RegisterWorkflowResponse {
    pub workflow_registry_id: String,
    pub version: String,
    pub content_hash: String,
    pub created_at: String,
    pub message: String,
}

/// Workflow version response
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkflowVersionResponse {
    pub namespace: String,
    pub name: String,
    pub version: String,
    pub is_latest: bool,
    pub content_hash: String,
    pub workflow_yaml: String,
    pub created_at: String,
}

/// List versions response
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ListVersionsResponse {
    pub versions: Vec<VersionInfo>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VersionInfo {
    pub version: String,
    pub is_latest: bool,
    pub content_hash: String,
    pub created_at: String,
}

/// Register a new workflow version (T023)
pub async fn register_workflow(
    State(state): State<AppState>,
    Json(req): Json<RegisterWorkflowRequest>,
) -> Result<(StatusCode, Json<RegisterWorkflowResponse>), SchedulerError> {
    // Validate namespace format (FR-022)
    let namespace_regex = Regex::new(r"^[a-z0-9_-]+$").unwrap();
    if !namespace_regex.is_match(&req.namespace) {
        return Err(SchedulerError::ValidationFailed(
            "Namespace must match pattern ^[a-z0-9_-]+$".to_string(),
        ));
    }

    // Validate workflow YAML against schema (FR-025)
    state
        .validator
        .validate_workflow_yaml(&req.workflow_yaml)
        .map_err(|e| {
            SchedulerError::ValidationFailed(format!("Workflow schema validation failed: {e}"))
        })?;

    // Calculate content hash (SHA-256 of normalized YAML)
    let content_hash = calculate_content_hash(&req.workflow_yaml)?;

    // Create workflow version
    let created_at = Utc::now();
    let workflow_version = WorkflowVersion {
        namespace: req.namespace.clone(),
        name: req.name.clone(),
        version: req.version.clone(),
        is_latest: req.is_latest,
        content_hash: content_hash.clone(),
        yaml_snapshot: req.workflow_yaml,
        git_repo: None,
        git_path: None,
        git_commit: None,
        git_branch: None,
        created_at: created_at.to_rfc3339(),
    };

    // Insert to database (will return 409 if duplicate)
    workflow_version::create(&state.db_pool, &workflow_version).await?;

    // Update is_latest if requested
    if req.is_latest {
        workflow_version::update_latest(&state.db_pool, &req.namespace, &req.name, &req.version)
            .await?;
    }

    Ok((
        StatusCode::CREATED,
        Json(RegisterWorkflowResponse {
            workflow_registry_id: format!("{}/{}", req.namespace, req.name),
            version: req.version,
            content_hash,
            created_at: created_at.to_rfc3339(),
            message: "Workflow registered successfully".to_string(),
        }),
    ))
}

/// Get workflow by reference (T024)
pub async fn get_workflow(
    State(state): State<AppState>,
    Path((namespace, name, version)): Path<(String, String, String)>,
) -> Result<Json<WorkflowVersionResponse>, SchedulerError> {
    // Fetch workflow version
    let wf = workflow_version::get_by_ref(&state.db_pool, &namespace, &name, &version)
        .await?
        .ok_or_else(|| {
            SchedulerError::NotFound(format!("Workflow {namespace}:{name}@{version} not found"))
        })?;

    Ok(Json(WorkflowVersionResponse {
        namespace: wf.namespace,
        name: wf.name,
        version: wf.version,
        is_latest: wf.is_latest,
        content_hash: wf.content_hash,
        workflow_yaml: wf.yaml_snapshot,
        created_at: wf.created_at,
    }))
}

/// List all versions for a workflow (T025)
pub async fn list_versions(
    State(state): State<AppState>,
    Path((namespace, name)): Path<(String, String)>,
) -> Result<Json<ListVersionsResponse>, SchedulerError> {
    let versions = workflow_version::list_versions(&state.db_pool, &namespace, &name).await?;

    let version_infos: Vec<VersionInfo> = versions
        .into_iter()
        .map(|v| VersionInfo {
            version: v.version,
            is_latest: v.is_latest,
            content_hash: v.content_hash,
            created_at: v.created_at,
        })
        .collect();

    Ok(Json(ListVersionsResponse {
        versions: version_infos,
    }))
}
