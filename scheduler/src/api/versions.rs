use axum::{Json, Router, http::header, response::IntoResponse, routing::get};
use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::app::AppState;

/// Shape version of this document itself.
pub const DISCOVERY_VERSION: u32 = 1;

/// Point versions of the REST contract this server serves.
pub const REST_VERSIONS: &[&str] = &["v1.0"];

/// Default number of events returned by a bounded event read.
pub const EVENTS_DEFAULT_PAGE: i64 = 100;

/// Largest number of events a single bounded event read may return.
pub const EVENTS_MAX_PAGE: i64 = 500;

/// Largest size a single string leaf may occupy in a served payload.
pub const EVENT_STRING_LEAF_MAX_BYTES: usize = 16 * 1024;

/// Size at which a served event payload is shortened.
pub const EVENT_PAYLOAD_BUDGET_BYTES: usize = 64 * 1024;

/// Default number of rows returned by a resolved-decisions page.
pub const DECISIONS_RESOLVED_DEFAULT_PAGE: i64 = 50;

/// Largest number of rows a resolved-decisions page may return.
pub const DECISIONS_RESOLVED_MAX_PAGE: i64 = 200;

/// Server build information. Diagnostic only.
#[derive(Debug, Serialize)]
pub struct ServerInfo {
    pub version: String,
}

/// Response body of `GET /api/versions`.
#[derive(Debug, Serialize)]
pub struct DiscoveryResponse {
    pub discovery_version: u32,
    pub server: ServerInfo,
    pub rest_versions: Vec<String>,
    pub limits: Map<String, Value>,
}

fn limits() -> Map<String, Value> {
    let mut limits = Map::new();
    limits.insert(
        "events".into(),
        json!({
            "default_page": EVENTS_DEFAULT_PAGE,
            "max_page": EVENTS_MAX_PAGE,
            "string_leaf_max_bytes": EVENT_STRING_LEAF_MAX_BYTES,
            "payload_budget_bytes": EVENT_PAYLOAD_BUDGET_BYTES,
        }),
    );
    limits.insert(
        "decisions".into(),
        json!({
            "resolved_default_page": DECISIONS_RESOLVED_DEFAULT_PAGE,
            "resolved_max_page": DECISIONS_RESOLVED_MAX_PAGE,
        }),
    );
    limits
}

async fn versions_handler() -> impl IntoResponse {
    let body = DiscoveryResponse {
        discovery_version: DISCOVERY_VERSION,
        server: ServerInfo {
            version: env!("CARGO_PKG_VERSION").to_string(),
        },
        rest_versions: REST_VERSIONS.iter().map(|v| v.to_string()).collect(),
        limits: limits(),
    };
    ([(header::CACHE_CONTROL, "no-store")], Json(body))
}

/// Version discovery route. Unversioned by design.
pub fn routes() -> Router<AppState> {
    Router::new().route("/api/versions", get(versions_handler))
}
