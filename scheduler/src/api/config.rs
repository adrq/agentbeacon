use axum::{Json, Router, extract::State, routing::get};
use serde::{Deserialize, Serialize};

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

/// Request body for updating configuration
#[derive(Debug, Deserialize)]
pub struct UpdateConfigRequest {
    pub name: String,
    pub value: String,
}

/// Configuration response matching OpenAPI spec
#[derive(Debug, Serialize)]
pub struct ConfigResponse {
    pub name: String,
    pub value: String,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::Config> for ConfigResponse {
    fn from(config: db::Config) -> Self {
        Self {
            name: config.name,
            value: config.value,
            created_at: config.created_at.to_rfc3339(),
            updated_at: config.updated_at.to_rfc3339(),
        }
    }
}

/// Get all configuration entries (GET /api/config)
async fn get_config(
    State(state): State<AppState>,
) -> Result<Json<Vec<ConfigResponse>>, SchedulerError> {
    let configs = db::config::list(&state.db_pool).await?;
    let responses: Vec<ConfigResponse> = configs.into_iter().map(Into::into).collect();
    Ok(Json(responses))
}

/// Update configuration entry (POST /api/config)
async fn update_config(
    State(state): State<AppState>,
    Json(payload): Json<UpdateConfigRequest>,
) -> Result<Json<ConfigResponse>, SchedulerError> {
    // Validate input
    if payload.name.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "Config name cannot be empty".to_string(),
        ));
    }
    if payload.value.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "Config value cannot be empty".to_string(),
        ));
    }

    // Upsert configuration
    db::config::upsert(&state.db_pool, &payload.name, &payload.value).await?;

    // Fetch the updated config
    let config = db::config::get(&state.db_pool, &payload.name).await?;

    Ok(Json(config.into()))
}

/// Configuration routes
pub fn routes() -> Router<AppState> {
    Router::new().route("/api/config", get(get_config).post(update_config))
}
