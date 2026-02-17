use std::path::Path;

use axum::{
    Json, Router,
    extract::{Path as AxumPath, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

#[derive(Debug, Serialize)]
pub struct ProjectResponse {
    pub id: String,
    pub name: String,
    pub path: String,
    pub default_agent_id: Option<String>,
    pub settings: serde_json::Value,
    pub is_git: bool,
    pub created_at: String,
    pub updated_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warning: Option<String>,
}

impl ProjectResponse {
    fn from_project(p: db::projects::Project, warning: Option<String>) -> Self {
        let settings = serde_json::from_str(&p.settings).unwrap_or_else(|_| serde_json::json!({}));
        let is_git = Path::new(&p.path).join(".git").is_dir();
        Self {
            id: p.id,
            name: p.name,
            path: p.path,
            default_agent_id: p.default_agent_id,
            settings,
            is_git,
            created_at: p.created_at.to_rfc3339(),
            updated_at: p.updated_at.to_rfc3339(),
            warning,
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct CreateProjectRequest {
    pub name: String,
    pub path: String,
    pub default_agent_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateProjectRequest {
    pub name: Option<String>,
    pub path: Option<String>,
    pub default_agent_id: Option<Option<String>>,
    pub settings: Option<serde_json::Value>,
}

async fn create_project(
    State(state): State<AppState>,
    Json(req): Json<CreateProjectRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Validate name
    let name = req.name.trim();
    if name.is_empty() || name.len() > 255 {
        return Err(SchedulerError::ValidationFailed(
            "name must be non-empty and max 255 chars".to_string(),
        ));
    }

    // Validate path: must be absolute, must exist, must be directory
    let path_str = req.path.trim();
    if !Path::new(path_str).is_absolute() {
        return Err(SchedulerError::ValidationFailed(format!(
            "path must be absolute: {path_str}"
        )));
    }
    let canonical = std::fs::canonicalize(path_str).map_err(|_| {
        SchedulerError::ValidationFailed(format!("path does not exist: {path_str}"))
    })?;
    if !canonical.is_dir() {
        return Err(SchedulerError::ValidationFailed(format!(
            "path is not a directory: {path_str}"
        )));
    }
    let canonical_str = canonical.to_string_lossy().to_string();

    // Validate default_agent_id if provided
    if let Some(ref agent_id) = req.default_agent_id {
        let agent = db::agents::get_by_id(&state.db_pool, agent_id)
            .await
            .map_err(|e| match e {
                SchedulerError::NotFound(_) => {
                    SchedulerError::ValidationFailed(format!("agent not found: {agent_id}"))
                }
                other => other,
            })?;
        if !agent.enabled {
            return Err(SchedulerError::ValidationFailed(format!(
                "agent is disabled: {agent_id}"
            )));
        }
    }

    // Duplicate path warning
    let path_count = db::projects::count_by_path(&state.db_pool, &canonical_str).await?;
    let warning = if path_count > 0 {
        Some("Another project already uses this path".to_string())
    } else {
        None
    };

    let id = Uuid::new_v4().to_string();
    let project = db::projects::create(
        &state.db_pool,
        &id,
        name,
        &canonical_str,
        req.default_agent_id.as_deref(),
        None,
    )
    .await?;

    Ok((
        StatusCode::CREATED,
        Json(ProjectResponse::from_project(project, warning)),
    ))
}

async fn list_projects(
    State(state): State<AppState>,
) -> Result<Json<Vec<ProjectResponse>>, SchedulerError> {
    let projects = db::projects::list(&state.db_pool).await?;
    Ok(Json(
        projects
            .into_iter()
            .map(|p| ProjectResponse::from_project(p, None))
            .collect(),
    ))
}

async fn get_project(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<ProjectResponse>, SchedulerError> {
    let project = db::projects::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(ProjectResponse::from_project(project, None)))
}

async fn update_project(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Json(req): Json<UpdateProjectRequest>,
) -> Result<Json<ProjectResponse>, SchedulerError> {
    // Validate name if provided
    if let Some(ref name) = req.name {
        let name = name.trim();
        if name.is_empty() || name.len() > 255 {
            return Err(SchedulerError::ValidationFailed(
                "name must be non-empty and max 255 chars".to_string(),
            ));
        }
    }

    // Validate and canonicalize path if provided
    let canonical_path = if let Some(ref path_str) = req.path {
        let path_str = path_str.trim();
        if !Path::new(path_str).is_absolute() {
            return Err(SchedulerError::ValidationFailed(format!(
                "path must be absolute: {path_str}"
            )));
        }
        let canonical = std::fs::canonicalize(path_str).map_err(|_| {
            SchedulerError::ValidationFailed(format!("path does not exist: {path_str}"))
        })?;
        if !canonical.is_dir() {
            return Err(SchedulerError::ValidationFailed(format!(
                "path is not a directory: {path_str}"
            )));
        }
        Some(canonical.to_string_lossy().to_string())
    } else {
        None
    };

    // Validate default_agent_id if provided and not null
    if let Some(Some(ref agent_id)) = req.default_agent_id {
        let agent = db::agents::get_by_id(&state.db_pool, agent_id)
            .await
            .map_err(|e| match e {
                SchedulerError::NotFound(_) => {
                    SchedulerError::ValidationFailed(format!("agent not found: {agent_id}"))
                }
                other => other,
            })?;
        if !agent.enabled {
            return Err(SchedulerError::ValidationFailed(format!(
                "agent is disabled: {agent_id}"
            )));
        }
    }

    let settings_json = req
        .settings
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "{}".to_string()));

    let default_agent = req.default_agent_id.as_ref().map(|opt| opt.as_deref());
    let trimmed_name = req.name.as_ref().map(|n| n.trim().to_string());

    let project = db::projects::update(
        &state.db_pool,
        &id,
        trimmed_name.as_deref(),
        canonical_path.as_deref(),
        default_agent,
        settings_json.as_deref(),
    )
    .await?;

    Ok(Json(ProjectResponse::from_project(project, None)))
}

async fn delete_project(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::projects::soft_delete(&state.db_pool, &id).await?;
    Ok(StatusCode::NO_CONTENT)
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/projects", get(list_projects).post(create_project))
        .route(
            "/api/projects/{id}",
            get(get_project)
                .patch(update_project)
                .delete(delete_project),
        )
}
