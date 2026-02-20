use axum::{
    Router,
    extract::{Request, State},
    response::Redirect,
    routing::get,
};
use std::sync::Arc;
use tokio::sync::broadcast;
use tower_http::{compression::CompressionLayer, cors::CorsLayer};
use tracing::warn;

use crate::assets::Assets;
use crate::db::DbPool;
use crate::queue::TaskQueue;

/// Notification sent when a new event is inserted into the events table.
/// SSE handlers subscribe to these to push updates in real-time.
#[derive(Clone, Debug)]
pub struct EventNotification {
    pub execution_id: String,
    pub event_id: i64,
}

/// Application state shared across handlers
#[derive(Clone)]
pub struct AppState {
    pub db_pool: DbPool,
    pub task_queue: Arc<TaskQueue>,
    pub base_url: String,
    pub public_url: Option<String>,
    pub event_broadcast: broadcast::Sender<EventNotification>,
    pub vite_dev_port: u16,
}

impl AppState {
    pub fn new(
        db_pool: DbPool,
        task_queue: Arc<TaskQueue>,
        base_url: String,
        public_url: Option<String>,
        port: u16,
    ) -> Self {
        let (event_broadcast, _) = broadcast::channel(256);
        let vite_dev_port = std::env::var("VITE_DEV_PORT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(port + 1000);
        Self {
            db_pool,
            task_queue,
            base_url,
            public_url,
            event_broadcast,
            vite_dev_port,
        }
    }

    /// Resolve the public base URL for this request
    ///
    /// Determines the base URL using the following priority:
    /// 1. PUBLIC_URL environment variable (explicit override)
    /// 2. X-Forwarded-Host and X-Forwarded-Proto headers (proxy/load balancer)
    /// 3. Base URL configured at startup (localhost:port)
    ///
    /// # Multi-proxy handling
    /// When X-Forwarded-* headers contain comma-separated lists (common in multi-proxy
    /// setups), this method extracts the first (leftmost) value, which represents the
    /// original client-facing host.
    pub fn resolve_base_url(&self, headers: &axum::http::HeaderMap) -> String {
        // Priority 1: PUBLIC_URL environment variable
        if let Some(ref public_url) = self.public_url {
            return public_url.clone();
        }

        // Priority 2: X-Forwarded-Host and X-Forwarded-Proto headers
        if let (Some(host), Some(proto)) = (
            headers.get("x-forwarded-host"),
            headers.get("x-forwarded-proto"),
        ) && let (Ok(host_str), Ok(proto_str)) = (host.to_str(), proto.to_str())
        {
            // In multi-proxy setups, these headers contain comma-separated lists.
            // Take the first (leftmost) value which is the original client value.
            let host_value = host_str.split(',').next().unwrap_or("").trim();
            let proto_value = proto_str
                .split(',')
                .next()
                .unwrap_or("")
                .trim()
                .to_lowercase();

            if !host_value.is_empty() && !proto_value.is_empty() {
                return format!("{proto_value}://{host_value}");
            }
        }

        // Priority 3: Fallback to localhost:port
        self.base_url.clone()
    }
}

/// Build Axum router with all routes and middleware
pub fn create_router(state: AppState, dev_mode: bool, port: u16) -> Router {
    let vite_dev_port = state.vite_dev_port;

    // SSE routes bypass compression (CompressionLayer buffers, breaking streaming)
    let sse_routes = crate::api::sse::routes();

    // Base routes get compression
    let base_router = Router::new().merge(crate::api::routes());
    let compressed = if dev_mode {
        base_router
            .route("/", get(dev_mode_redirect_root))
            .fallback(dev_mode_redirect_path)
    } else {
        base_router
            .route("/", get(serve_index))
            .route("/index.html", get(serve_index))
            .fallback(serve_spa_fallback)
    }
    .layer(CompressionLayer::new());

    // Merge SSE (uncompressed) + base (compressed), then apply CORS at the outer level
    Router::new()
        .merge(sse_routes)
        .merge(compressed)
        .layer(build_cors_layer(dev_mode, port, vite_dev_port))
        .with_state(state)
}

/// Build CORS layer with restricted origins for security
///
/// Default allowed origins:
/// - Development mode: http://localhost:{vite_dev_port} (Vite dev server)
/// - Production mode: http://localhost:{port} (embedded UI)
///
/// Additional origins can be configured via CORS_ALLOWED_ORIGINS environment variable
/// (comma-separated list of origins, e.g., "http://localhost:3000,http://localhost:8080")
fn build_cors_layer(dev_mode: bool, port: u16, vite_dev_port: u16) -> CorsLayer {
    use axum::http::{
        HeaderValue, Method,
        header::{AUTHORIZATION, CONTENT_TYPE},
    };
    use tower_http::cors::{AllowHeaders, AllowMethods, AllowOrigin};

    let mut allowed_origins = Vec::new();

    if dev_mode {
        allowed_origins.push(format!("http://localhost:{vite_dev_port}"));
    }
    allowed_origins.push(format!("http://localhost:{port}"));

    if let Ok(custom_origins) = std::env::var("CORS_ALLOWED_ORIGINS") {
        for origin in custom_origins.split(',') {
            let trimmed = origin.trim();
            if !trimmed.is_empty() && !allowed_origins.contains(&trimmed.to_string()) {
                allowed_origins.push(trimmed.to_string());
            }
        }
    }

    let origin_values: Vec<HeaderValue> = allowed_origins
        .iter()
        .filter_map(|origin| match origin.parse::<HeaderValue>() {
            Ok(value) => Some(value),
            Err(e) => {
                warn!(
                    origin = %origin,
                    error = %e,
                    "Invalid CORS origin in configuration; skipping"
                );
                None
            }
        })
        .collect();

    CorsLayer::new()
        .allow_origin(AllowOrigin::list(origin_values))
        .allow_methods(AllowMethods::list([
            Method::GET,
            Method::POST,
            Method::PUT,
            Method::DELETE,
            Method::OPTIONS,
        ]))
        .allow_headers(AllowHeaders::list([
            CONTENT_TYPE,
            AUTHORIZATION,
            axum::http::HeaderName::from_static("last-event-id"),
        ]))
        .allow_credentials(true)
}

async fn dev_mode_redirect_root(State(state): State<AppState>) -> Redirect {
    Redirect::temporary(&format!("http://localhost:{}", state.vite_dev_port))
}

/// Development mode - redirect all other paths to Vite dev server
async fn dev_mode_redirect_path(State(state): State<AppState>, req: Request) -> Redirect {
    let path = req.uri().path();
    let redirect_url = format!("http://localhost:{}{path}", state.vite_dev_port);
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
