use axum::{Router, extract::Request, response::Redirect, routing::get};
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
pub fn create_router(state: AppState, dev_mode: bool) -> Router {
    let base_router = Router::new().merge(crate::api::routes());

    let router = if dev_mode {
        // Development mode - redirect all UI routes to Vite dev server
        base_router
            .route("/", get(dev_mode_redirect_root))
            .fallback(dev_mode_redirect_path)
    } else {
        // Production mode - serve embedded static files (use fallback for all non-API routes)
        base_router
            .route("/", get(serve_index))
            .route("/index.html", get(serve_index))
            .fallback(serve_spa_fallback)
    };

    router
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .layer(CompressionLayer::new())
        .with_state(state)
}

/// Development mode - redirect root to Vite dev server
async fn dev_mode_redirect_root() -> Redirect {
    Redirect::temporary("http://localhost:5173")
}

/// Development mode - redirect all other paths to Vite dev server
async fn dev_mode_redirect_path(req: Request) -> Redirect {
    let path = req.uri().path();
    let redirect_url = format!("http://localhost:5173{}", path);
    Redirect::temporary(&redirect_url)
}

/// Serve index.html
async fn serve_index() -> axum::response::Response {
    Assets::serve("index.html")
}

/// SPA fallback - try to serve asset, otherwise serve index.html
async fn serve_spa_fallback(req: Request) -> axum::response::Response {
    let path = req.uri().path().trim_start_matches('/');

    // Try to serve the requested asset first
    if !path.is_empty() && path != "index.html" {
        Assets::serve(path)
    } else {
        // Default to index.html for root and unmatched routes
        Assets::serve_spa_fallback()
    }
}
