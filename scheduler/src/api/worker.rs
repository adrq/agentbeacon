use axum::{Router, routing::post};

use crate::api::handlers;
use crate::app::AppState;

/// Create worker API router
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/worker/sync", post(handlers::handle_worker_sync))
        .route("/api/worker/events", post(handlers::handle_worker_event))
}
