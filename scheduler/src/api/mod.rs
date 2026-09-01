use axum::{Router, routing::any};

use crate::api::problem::{Problem, ProblemCode};
use crate::app::AppState;

pub mod agent_card;
pub mod agents;
pub mod auth;
pub mod config;
pub mod decisions;
pub mod docs;
pub mod drivers;
pub mod escalate;
pub mod executions;
pub mod handlers;
pub mod health;
pub mod jsonrpc;
pub mod mcp;
pub mod mcp_servers;
pub mod mcp_tools;
pub mod messages;
pub mod problem;
pub mod projects;
pub mod sessions;
pub mod sse;
pub mod types;
pub mod v1;
pub mod versions;
pub mod wiki;
pub mod wiki_auth;
pub mod wiki_tags;
pub mod worker;

async fn api_path_not_found() -> Problem {
    Problem::new(ProblemCode::ResourceNotFound).with_detail("no route matches this path")
}

/// Build the router for everything served outside the versioned product
/// contract: the operational probes, the docs locator, version discovery, the
/// worker control plane and the A2A/MCP protocol endpoints.
///
/// The versioned product routes live in [`v1::routes`] and are nested under
/// `/api/v1` by the application router.
pub fn routes() -> Router<AppState> {
    Router::new()
        .merge(health::routes())
        .merge(versions::routes())
        .merge(docs::routes())
        .merge(jsonrpc::routes())
        .merge(agent_card::routes())
        .merge(worker::routes())
        .merge(mcp::routes())
        // Unmatched paths below /api answer with a problem instead of falling
        // through to the SPA.
        .route("/api", any(api_path_not_found))
        // axum matches these three patterns distinctly: the wildcard needs at
        // least one tail segment, and there is no trailing-slash redirect.
        .route("/api/", any(api_path_not_found))
        .route("/api/{*rest}", any(api_path_not_found))
}
