use axum::{Router, routing::get};
use std::sync::Arc;
use tower_http::{
    compression::CompressionLayer,
    cors::{Any, CorsLayer},
};

use crate::assets::Assets;
use crate::db::DbPool;
use crate::queue::TaskQueue;
use crate::scheduling::scheduler::Scheduler;
use crate::validation::SchemaValidator;

/// Application state shared across handlers
#[derive(Clone)]
pub struct AppState {
    pub db_pool: DbPool,
    pub validator: Arc<SchemaValidator>,
    pub task_queue: Arc<TaskQueue>,
    pub scheduler: Arc<Scheduler>,
}

impl AppState {
    /// Create new application state
    pub fn new(
        db_pool: DbPool,
        validator: Arc<SchemaValidator>,
        task_queue: Arc<TaskQueue>,
        scheduler: Scheduler,
    ) -> Self {
        Self {
            db_pool,
            validator,
            task_queue,
            scheduler: Arc::new(scheduler),
        }
    }
}

/// Build Axum router with all routes and middleware
pub fn create_router(state: AppState) -> Router {
    // Merge with asset routes and apply state
    Router::new()
        .merge(crate::api::routes())
        .route("/", get(serve_index))
        .route("/index.html", get(serve_index))
        .route("/assets/{*path}", get(serve_asset))
        .fallback(serve_spa_fallback)
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .layer(CompressionLayer::new())
        .with_state(state)
}

/// Serve index.html
async fn serve_index() -> axum::response::Response {
    Assets::serve("index.html")
}

/// Serve asset by path
async fn serve_asset(
    axum::extract::Path(path): axum::extract::Path<String>,
) -> axum::response::Response {
    Assets::serve(&format!("assets/{path}"))
}

/// SPA fallback - serve index.html for all non-API routes
async fn serve_spa_fallback() -> axum::response::Response {
    Assets::serve_spa_fallback()
}
