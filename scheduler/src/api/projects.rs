use std::path::Path;

use axum::{
    Json, Router,
    extract::{Path as AxumPath, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use tracing::warn;
use uuid::Uuid;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

#[derive(Debug, Serialize)]
pub struct ProjectResponse {
    pub id: String,
    pub name: String,
    pub path: String,
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
}

#[derive(Debug, Deserialize)]
pub struct UpdateProjectRequest {
    pub name: Option<String>,
    pub path: Option<String>,
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

    // Duplicate path warning
    let path_count = db::projects::count_by_path(&state.db_pool, &canonical_str).await?;
    let warning = if path_count > 0 {
        Some("Another project already uses this path".to_string())
    } else {
        None
    };

    let id = Uuid::new_v4().to_string();
    let project = db::projects::create(&state.db_pool, &id, name, &canonical_str, None).await?;

    // Seed project_agents with all enabled agents
    let all_agents = db::agents::list(&state.db_pool).await?;
    for agent in &all_agents {
        if agent.enabled
            && let Err(e) = db::project_agents::insert(&state.db_pool, &id, &agent.id).await
        {
            warn!(project_id = %id, agent_id = %agent.id, error = %e, "failed to seed agent into project pool");
        }
    }

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

    let settings_json = req
        .settings
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "{}".to_string()));

    let trimmed_name = req.name.as_ref().map(|n| n.trim().to_string());

    let project = db::projects::update(
        &state.db_pool,
        &id,
        trimmed_name.as_deref(),
        canonical_path.as_deref(),
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

// --- Project agent pool sub-resources ---

async fn list_project_agents(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Vec<db::project_agents::AgentPoolEntry>>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &id).await?;
    let entries = db::project_agents::list_by_project(&state.db_pool, &id).await?;
    Ok(Json(entries))
}

#[derive(Debug, Deserialize)]
struct AddProjectAgentRequest {
    agent_id: String,
}

async fn add_project_agent(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Json(req): Json<AddProjectAgentRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &id).await?;
    // Verify agent exists and is enabled
    let agent = db::agents::get_by_id(&state.db_pool, &req.agent_id).await?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent is disabled: {}",
            req.agent_id
        )));
    }
    db::project_agents::insert(&state.db_pool, &id, &req.agent_id).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn remove_project_agent(
    State(state): State<AppState>,
    AxumPath((id, agent_id)): AxumPath<(String, String)>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &id).await?;
    let deleted = db::project_agents::delete(&state.db_pool, &id, &agent_id).await?;
    if !deleted {
        return Err(SchedulerError::NotFound(format!(
            "agent {agent_id} not in project pool"
        )));
    }
    Ok(StatusCode::NO_CONTENT)
}

// --- Project MCP server pool sub-resources ---

async fn list_project_mcp_servers(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Vec<db::project_mcp_servers::McpServerPoolEntry>>, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &id).await?;
    let entries = db::project_mcp_servers::list_by_project(&state.db_pool, &id).await?;
    Ok(Json(entries))
}

#[derive(Debug, Deserialize)]
struct AddProjectMcpServerRequest {
    mcp_server_id: String,
}

async fn add_project_mcp_server(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Json(req): Json<AddProjectMcpServerRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &id).await?;
    // Verify MCP server exists
    db::mcp_servers::get_by_id(&state.db_pool, &req.mcp_server_id).await?;
    db::project_mcp_servers::insert(&state.db_pool, &id, &req.mcp_server_id).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn remove_project_mcp_server(
    State(state): State<AppState>,
    AxumPath((id, mcp_server_id)): AxumPath<(String, String)>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &id).await?;
    let deleted = db::project_mcp_servers::delete(&state.db_pool, &id, &mcp_server_id).await?;
    if !deleted {
        return Err(SchedulerError::NotFound(format!(
            "MCP server {mcp_server_id} not in project pool"
        )));
    }
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
        .route(
            "/api/projects/{id}/agents",
            get(list_project_agents).post(add_project_agent),
        )
        .route(
            "/api/projects/{id}/agents/{agent_id}",
            axum::routing::delete(remove_project_agent),
        )
        .route(
            "/api/projects/{id}/mcp-servers",
            get(list_project_mcp_servers).post(add_project_mcp_server),
        )
        .route(
            "/api/projects/{id}/mcp-servers/{mcp_server_id}",
            axum::routing::delete(remove_project_mcp_server),
        )
}
