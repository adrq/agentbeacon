use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::json;

use crate::app::AppState;

/// Health check handler - liveness probe (no dependency checks)
async fn health_handler() -> Json<serde_json::Value> {
    Json(json!({"status": "healthy"}))
}

/// Ready check handler - readiness probe (checks DB connectivity)
async fn ready_handler(State(state): State<AppState>) -> Response {
    match sqlx::query("SELECT 1")
        .fetch_one(state.db_pool.as_ref())
        .await
    {
        Ok(_) => (StatusCode::OK, Json(json!({"status": "ready"}))).into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"status": "not_ready"})),
        )
            .into_response(),
    }
}

/// Health and ready check routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/health", get(health_handler))
        .route("/api/ready", get(ready_handler))
}
