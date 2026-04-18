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

pub const VALID_PLATFORMS: &[&str] = &["claude_sdk", "codex_sdk", "copilot_sdk", "acp"];

#[derive(Debug, Serialize)]
pub struct DriverResponse {
    pub id: String,
    pub name: String,
    pub platform: String,
    pub config: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::drivers::Driver> for DriverResponse {
    fn from(d: db::drivers::Driver) -> Self {
        let config = serde_json::from_str(&d.config).unwrap_or_else(|e| {
            tracing::warn!(driver_id = %d.id, error = %e, "invalid JSON in driver config, falling back to {{}}");
            serde_json::json!({})
        });
        Self {
            id: d.id,
            name: d.name,
            platform: d.platform,
            config,
            created_at: d.created_at.to_rfc3339(),
            updated_at: d.updated_at.to_rfc3339(),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct CreateDriverRequest {
    pub name: String,
    pub platform: String,
    pub config: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateDriverRequest {
    pub name: Option<String>,
    pub platform: Option<serde_json::Value>, // presence triggers 400
    pub config: Option<serde_json::Value>,
}

async fn list_drivers(
    State(state): State<AppState>,
) -> Result<Json<Vec<DriverResponse>>, SchedulerError> {
    let drivers = db::drivers::list(&state.db_pool).await?;
    Ok(Json(
        drivers
            .into_iter()
            .filter(|d| VALID_PLATFORMS.contains(&d.platform.as_str()))
            .map(Into::into)
            .collect(),
    ))
}

async fn get_driver(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<DriverResponse>, SchedulerError> {
    let driver = db::drivers::get_by_id(&state.db_pool, &id).await?;
    if !VALID_PLATFORMS.contains(&driver.platform.as_str()) {
        return Err(SchedulerError::NotFound(format!("driver not found: {id}")));
    }
    Ok(Json(driver.into()))
}

async fn create_driver(
    State(state): State<AppState>,
    Json(req): Json<CreateDriverRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let name = req.name.trim();
    if name.is_empty() || name.len() > 255 {
        return Err(SchedulerError::ValidationFailed(
            "name must be non-empty and max 255 chars".to_string(),
        ));
    }

    if !VALID_PLATFORMS.contains(&req.platform.as_str()) {
        return Err(SchedulerError::ValidationFailed(format!(
            "invalid platform: {}. Must be one of: {}",
            req.platform,
            VALID_PLATFORMS.join(", ")
        )));
    }

    // Check platform uniqueness before INSERT for a clear error message
    if db::drivers::get_by_platform(&state.db_pool, &req.platform)
        .await
        .is_ok()
    {
        return Err(SchedulerError::Conflict(format!(
            "a driver already exists for platform: {}",
            req.platform
        )));
    }

    let config = req.config.unwrap_or(serde_json::json!({}));
    if !config.is_object() {
        return Err(SchedulerError::ValidationFailed(
            "config must be a JSON object".to_string(),
        ));
    }

    let id = Uuid::new_v4().to_string();
    let config_str = serde_json::to_string(&config)
        .map_err(|e| SchedulerError::ValidationFailed(format!("invalid config JSON: {e}")))?;

    db::drivers::create(&state.db_pool, &id, name, &req.platform, &config_str).await?;

    let driver = db::drivers::get_by_id(&state.db_pool, &id).await?;
    Ok((StatusCode::CREATED, Json(DriverResponse::from(driver))))
}

async fn update_driver(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<UpdateDriverRequest>,
) -> Result<Json<DriverResponse>, SchedulerError> {
    // Reject updates to retired drivers
    let existing = db::drivers::get_by_id(&state.db_pool, &id).await?;
    if !VALID_PLATFORMS.contains(&existing.platform.as_str()) {
        return Err(SchedulerError::NotFound(format!("driver not found: {id}")));
    }

    if req.platform.is_some() {
        return Err(SchedulerError::ValidationFailed(
            "platform is immutable".to_string(),
        ));
    }

    if let Some(ref name) = req.name {
        let name = name.trim();
        if name.is_empty() || name.len() > 255 {
            return Err(SchedulerError::ValidationFailed(
                "name must be non-empty and max 255 chars".to_string(),
            ));
        }
    }

    if req.config.as_ref().is_some_and(|c| !c.is_object()) {
        return Err(SchedulerError::ValidationFailed(
            "config must be a JSON object".to_string(),
        ));
    }

    let config_str = req
        .config
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "{}".to_string()));

    let trimmed_name = req.name.as_ref().map(|n| n.trim().to_string());

    let driver = db::drivers::update(
        &state.db_pool,
        &id,
        trimmed_name.as_deref(),
        config_str.as_deref(),
    )
    .await?;

    Ok(Json(driver.into()))
}

async fn get_driver_descriptor(
    Path(platform): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    if !VALID_PLATFORMS.contains(&platform.as_str()) {
        return Err(SchedulerError::NotFound(format!(
            "no descriptor for platform: {platform}"
        )));
    }
    let descriptor =
        crate::services::driver_descriptors::get_descriptor(&platform).ok_or_else(|| {
            SchedulerError::NotFound(format!("no descriptor for platform: {platform}"))
        })?;
    let value = serde_json::to_value(descriptor)
        .map_err(|e| SchedulerError::Database(format!("failed to serialize descriptor: {e}")))?;
    Ok(Json(value))
}

async fn get_driver_models(
    Path(platform): Path<String>,
) -> Result<Json<Vec<crate::services::model_catalog::ModelSuggestion>>, SchedulerError> {
    if !VALID_PLATFORMS.contains(&platform.as_str()) {
        return Err(SchedulerError::NotFound(format!(
            "no models for platform: {platform}"
        )));
    }
    let models = crate::services::model_catalog::list_models(&platform).await;
    Ok(Json(models))
}

async fn delete_driver(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Reject deletes on retired drivers
    let existing = db::drivers::get_by_id(&state.db_pool, &id).await?;
    if !VALID_PLATFORMS.contains(&existing.platform.as_str()) {
        return Err(SchedulerError::NotFound(format!("driver not found: {id}")));
    }

    // Guard: reject delete if agents reference this driver
    let agent_count = db::drivers::count_agents_by_driver(&state.db_pool, &id).await?;
    if agent_count > 0 {
        return Err(SchedulerError::Conflict(format!(
            "driver has {agent_count} agent(s)"
        )));
    }

    db::drivers::hard_delete(&state.db_pool, &id).await?;

    Ok(StatusCode::NO_CONTENT)
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/drivers", get(list_drivers).post(create_driver))
        .route(
            "/api/drivers/{id}",
            get(get_driver).patch(update_driver).delete(delete_driver),
        )
        .route(
            "/api/drivers/{platform}/descriptor",
            get(get_driver_descriptor),
        )
        .route("/api/drivers/{platform}/models", get(get_driver_models))
}
