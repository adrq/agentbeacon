use axum::{
    Json, Router,
    extract::{Path, State},
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
pub struct AgentResponse {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub agent_type: String,
    pub driver_id: Option<String>,
    pub config: serde_json::Value,
    pub sandbox_config: Option<serde_json::Value>,
    pub enabled: bool,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::agents::Agent> for AgentResponse {
    fn from(a: db::agents::Agent) -> Self {
        let config = serde_json::from_str(&a.config).unwrap_or_else(|e| {
            tracing::warn!(agent_id = %a.id, error = %e, "invalid JSON in agent config, falling back to {{}}");
            serde_json::json!({})
        });
        let sandbox_config = a.sandbox_config.as_ref().map(|s| {
            serde_json::from_str(s).unwrap_or_else(|e| {
                tracing::warn!(agent_id = %a.id, error = %e, "invalid JSON in agent sandbox_config");
                serde_json::Value::Null
            })
        });
        Self {
            id: a.id,
            name: a.name,
            description: a.description,
            agent_type: a.agent_type,
            driver_id: a.driver_id,
            config,
            sandbox_config,
            enabled: a.enabled,
            created_at: a.created_at.to_rfc3339(),
            updated_at: a.updated_at.to_rfc3339(),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct CreateAgentRequest {
    pub name: String,
    pub description: Option<String>,
    pub driver_id: String,
    pub config: serde_json::Value,
    pub sandbox_config: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateAgentRequest {
    pub name: Option<String>,
    pub description: Option<Option<String>>,
    pub driver_id: Option<serde_json::Value>, // presence triggers 400
    pub config: Option<serde_json::Value>,
    pub sandbox_config: Option<Option<serde_json::Value>>,
    pub enabled: Option<bool>,
}

async fn list_agents(
    State(state): State<AppState>,
) -> Result<Json<Vec<AgentResponse>>, SchedulerError> {
    let agents = db::agents::list(&state.db_pool).await?;
    Ok(Json(agents.into_iter().map(Into::into).collect()))
}

async fn get_agent(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<AgentResponse>, SchedulerError> {
    let agent = db::agents::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(agent.into()))
}

async fn create_agent(
    State(state): State<AppState>,
    Json(req): Json<CreateAgentRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Validate name
    let name = req.name.trim();
    if name.is_empty() || name.len() > 255 {
        return Err(SchedulerError::ValidationFailed(
            "name must be non-empty and max 255 chars".to_string(),
        ));
    }

    // Validate description length
    if let Some(ref desc) = req.description
        && desc.len() > 1000
    {
        return Err(SchedulerError::ValidationFailed(
            "description must be max 1000 chars".to_string(),
        ));
    }

    // Look up driver, derive agent_type from driver.platform
    let driver = db::drivers::get_by_id(&state.db_pool, &req.driver_id)
        .await
        .map_err(|e| match e {
            SchedulerError::NotFound(_) => {
                SchedulerError::ValidationFailed(format!("driver not found: {}", req.driver_id))
            }
            other => other,
        })?;
    let resolved_agent_type = driver.platform;
    let resolved_driver_id = driver.id;

    // Validate config is a JSON object
    if !req.config.is_object() {
        return Err(SchedulerError::ValidationFailed(
            "config must be a JSON object".to_string(),
        ));
    }

    let id = Uuid::new_v4().to_string();
    let config_str = serde_json::to_string(&req.config)
        .map_err(|e| SchedulerError::ValidationFailed(format!("invalid config JSON: {e}")))?;
    let sandbox_str = req
        .sandbox_config
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "null".to_string()));

    db::agents::create(
        &state.db_pool,
        &id,
        name,
        &resolved_agent_type,
        &config_str,
        req.description.as_deref(),
        sandbox_str.as_deref(),
        Some(&resolved_driver_id),
    )
    .await?;

    let agent = db::agents::get_by_id(&state.db_pool, &id).await?;
    Ok((StatusCode::CREATED, Json(AgentResponse::from(agent))))
}

async fn update_agent(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<UpdateAgentRequest>,
) -> Result<Json<AgentResponse>, SchedulerError> {
    // Reject driver_id in body (immutable)
    if req.driver_id.is_some() {
        return Err(SchedulerError::ValidationFailed(
            "driver_id is immutable".to_string(),
        ));
    }

    // Validate name if provided
    if let Some(ref name) = req.name {
        let name = name.trim();
        if name.is_empty() || name.len() > 255 {
            return Err(SchedulerError::ValidationFailed(
                "name must be non-empty and max 255 chars".to_string(),
            ));
        }
    }

    let config_str = req
        .config
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "{}".to_string()));

    let sandbox_config = req.sandbox_config.as_ref().map(|opt| {
        opt.as_ref()
            .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "null".to_string()))
    });

    let description = req.description.as_ref().map(|opt| opt.as_deref());

    let sandbox_ref = sandbox_config.as_ref().map(|opt| opt.as_deref());

    let trimmed_name = req.name.as_ref().map(|n| n.trim().to_string());

    let agent = db::agents::update(
        &state.db_pool,
        &id,
        trimmed_name.as_deref(),
        description,
        config_str.as_deref(),
        sandbox_ref,
        req.enabled,
    )
    .await?;

    Ok(Json(agent.into()))
}

async fn delete_agent(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Check for non-terminal sessions
    let active_count = db::sessions::count_non_terminal_by_agent(&state.db_pool, &id).await?;
    if active_count > 0 {
        return Err(SchedulerError::Conflict(format!(
            "agent has {active_count} active session(s)"
        )));
    }

    // Clear default_agent_id on projects referencing this agent
    db::projects::clear_default_agent(&state.db_pool, &id).await?;

    // Soft delete
    db::agents::soft_delete(&state.db_pool, &id).await?;

    Ok(StatusCode::NO_CONTENT)
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/agents", get(list_agents).post(create_agent))
        .route(
            "/api/agents/{id}",
            get(get_agent).patch(update_agent).delete(delete_agent),
        )
}
