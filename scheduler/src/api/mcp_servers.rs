use std::sync::LazyLock;

use axum::{
    Json, Router,
    extract::{Path as AxumPath, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use jsonschema::Validator;
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

#[derive(Debug, Serialize)]
pub struct McpServerResponse {
    pub id: String,
    pub name: String,
    pub transport_type: String,
    pub config: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
}

impl McpServerResponse {
    fn from_server(s: db::mcp_servers::McpServer) -> Self {
        Self {
            id: s.id,
            name: s.name,
            transport_type: s.transport_type,
            config: s.config,
            created_at: s.created_at.to_rfc3339(),
            updated_at: s.updated_at.to_rfc3339(),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct CreateMcpServerRequest {
    pub name: String,
    pub transport_type: String,
    pub config: serde_json::Value,
}

#[derive(Debug, Deserialize)]
pub struct UpdateMcpServerRequest {
    pub name: Option<String>,
    pub transport_type: Option<String>,
    pub config: Option<serde_json::Value>,
}

fn validate_reserved_name(name: &str) -> Result<(), SchedulerError> {
    if name.to_lowercase() == "agentbeacon" {
        return Err(SchedulerError::ValidationFailed(
            "name 'agentbeacon' is reserved".to_string(),
        ));
    }
    Ok(())
}

static STDIO_CONFIG_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&json!({
        "type": "object",
        "required": ["command"],
        "additionalProperties": false,
        "properties": {
            "command": { "type": "string", "minLength": 1 },
            "args": {
                "type": "array",
                "items": { "type": "string" }
            },
            "env": {
                "type": "object",
                "additionalProperties": { "type": "string" }
            }
        }
    }))
    .expect("stdio config schema must compile")
});

static HTTP_CONFIG_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&json!({
        "type": "object",
        "required": ["url"],
        "additionalProperties": false,
        "properties": {
            "url": { "type": "string", "minLength": 1 },
            "headers": {
                "type": "object",
                "additionalProperties": { "type": "string" }
            }
        }
    }))
    .expect("http config schema must compile")
});

fn validate_config(transport_type: &str, config: &serde_json::Value) -> Result<(), SchedulerError> {
    let validator = match transport_type {
        "stdio" => &*STDIO_CONFIG_VALIDATOR,
        "http" => &*HTTP_CONFIG_VALIDATOR,
        _ => {
            return Err(SchedulerError::ValidationFailed(format!(
                "transport_type must be 'stdio' or 'http', got '{transport_type}'"
            )));
        }
    };

    validator.validate(config).map_err(|err| {
        let path = err.instance_path.to_string();
        if path.is_empty() {
            SchedulerError::ValidationFailed(format!("config: {err}"))
        } else {
            SchedulerError::ValidationFailed(format!("config{path}: {err}"))
        }
    })
}

async fn create_mcp_server(
    State(state): State<AppState>,
    Json(req): Json<CreateMcpServerRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let name = req.name.trim().to_string();
    if name.is_empty() || name.len() > 255 {
        return Err(SchedulerError::ValidationFailed(
            "name must be non-empty and max 255 chars".to_string(),
        ));
    }
    validate_reserved_name(&name)?;

    // Strip 'type' key from config if present — type is derived from transport_type
    let mut config = req.config;
    if let Some(obj) = config.as_object_mut() {
        obj.remove("type");
    }

    validate_config(&req.transport_type, &config)?;

    let id = Uuid::new_v4().to_string();
    let config_str = serde_json::to_string(&config).unwrap_or_else(|_| "{}".to_string());

    let server =
        db::mcp_servers::create(&state.db_pool, &id, &name, &req.transport_type, &config_str)
            .await?;

    Ok((
        StatusCode::CREATED,
        Json(McpServerResponse::from_server(server)),
    ))
}

async fn list_mcp_servers(
    State(state): State<AppState>,
) -> Result<Json<Vec<McpServerResponse>>, SchedulerError> {
    let servers = db::mcp_servers::list(&state.db_pool).await?;
    Ok(Json(
        servers
            .into_iter()
            .map(McpServerResponse::from_server)
            .collect(),
    ))
}

async fn get_mcp_server(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<McpServerResponse>, SchedulerError> {
    let server = db::mcp_servers::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(McpServerResponse::from_server(server)))
}

async fn update_mcp_server(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Json(req): Json<UpdateMcpServerRequest>,
) -> Result<Json<McpServerResponse>, SchedulerError> {
    // Validate name if provided
    let trimmed_name = if let Some(ref name) = req.name {
        let name = name.trim();
        if name.is_empty() || name.len() > 255 {
            return Err(SchedulerError::ValidationFailed(
                "name must be non-empty and max 255 chars".to_string(),
            ));
        }
        validate_reserved_name(name)?;
        Some(name.to_string())
    } else {
        None
    };

    // If transport_type actually changes, config must also be provided
    if let Some(ref new_tt) = req.transport_type
        && req.config.is_none()
    {
        let existing = db::mcp_servers::get_by_id(&state.db_pool, &id).await?;
        if new_tt != &existing.transport_type {
            return Err(SchedulerError::ValidationFailed(
                "config must be provided when changing transport_type".to_string(),
            ));
        }
    }

    // Strip 'type' key then validate config against effective transport_type
    let config = req.config.map(|mut c| {
        if let Some(obj) = c.as_object_mut() {
            obj.remove("type");
        }
        c
    });
    if let Some(ref config) = config {
        let effective_type = if let Some(ref tt) = req.transport_type {
            tt.clone()
        } else {
            let existing = db::mcp_servers::get_by_id(&state.db_pool, &id).await?;
            existing.transport_type
        };
        validate_config(&effective_type, config)?;
    }
    let config_str = config
        .as_ref()
        .map(|c| serde_json::to_string(c).unwrap_or_else(|_| "{}".to_string()));

    let server = db::mcp_servers::update(
        &state.db_pool,
        &id,
        trimmed_name.as_deref(),
        req.transport_type.as_deref(),
        config_str.as_deref(),
    )
    .await?;

    Ok(Json(McpServerResponse::from_server(server)))
}

async fn delete_mcp_server(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Pre-check: prevent delete if attached to projects
    let count = db::mcp_servers::count_project_attachments(&state.db_pool, &id).await?;
    if count > 0 {
        return Err(SchedulerError::Conflict(
            "Cannot delete: server is attached to one or more projects. Detach it first."
                .to_string(),
        ));
    }

    // Clean up junction rows for soft-deleted projects with no active executions
    db::mcp_servers::cleanup_orphaned_attachments(&state.db_pool, &id).await?;

    // Check if remaining attachments exist on soft-deleted projects with live executions
    let active_count =
        db::mcp_servers::count_active_deleted_project_attachments(&state.db_pool, &id).await?;
    if active_count > 0 {
        return Err(SchedulerError::Conflict(
            "Cannot delete: server is still in use by active executions. \
             Wait for executions to finish or cancel them first."
                .to_string(),
        ));
    }

    db::mcp_servers::delete(&state.db_pool, &id).await?;
    Ok(StatusCode::NO_CONTENT)
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route(
            "/api/mcp-servers",
            get(list_mcp_servers).post(create_mcp_server),
        )
        .route(
            "/api/mcp-servers/{id}",
            get(get_mcp_server)
                .patch(update_mcp_server)
                .delete(delete_mcp_server),
        )
}
