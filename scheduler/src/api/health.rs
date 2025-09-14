use axum::{Json, Router, routing::get};
use serde_json::json;

use crate::app::AppState;

/// Health check handler returning server status (FR-COMPAT-006)
async fn health_handler() -> Json<serde_json::Value> {
    Json(json!({
        "status": "healthy",
        "version": "1.0.0"
    }))
}

/// Health check routes
pub fn routes() -> Router<AppState> {
    Router::new().route("/api/health", get(health_handler))
}
