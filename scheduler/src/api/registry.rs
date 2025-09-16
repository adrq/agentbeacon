use axum::{
    Router,
    routing::{get, post},
};

use crate::api::handlers;
use crate::app::AppState;

/// Create registry API router
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/registry/workflows", post(handlers::register_workflow))
        .route(
            "/api/registry/workflows/:namespace/:name/:version",
            get(handlers::get_workflow),
        )
        .route(
            "/api/registry/workflows/:namespace/:name",
            get(handlers::list_versions),
        )
}
