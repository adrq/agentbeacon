use axum::Router;

use crate::app::AppState;

pub mod agent_card;
pub mod agents;
pub mod auth;
pub mod config;
pub mod docs;
pub mod drivers;
pub mod executions;
pub mod handlers;
pub mod health;
pub mod jsonrpc;
pub mod mcp;
pub mod mcp_tools;
pub mod messages;
pub mod projects;
pub mod sessions;
pub mod sse;
pub mod types;
pub mod wiki;
pub mod worker;

/// Build API router with all endpoint modules.
/// Note: SSE routes (sse::routes()) are intentionally excluded here — they are
/// merged separately in create_router() to bypass the compression layer.
pub fn routes() -> Router<AppState> {
    Router::new()
        .merge(health::routes())
        .merge(agents::routes())
        .merge(drivers::routes())
        .merge(executions::routes())
        .merge(projects::routes())
        .merge(config::routes())
        .merge(docs::routes())
        .merge(jsonrpc::routes())
        .merge(agent_card::routes())
        .merge(worker::routes())
        .merge(mcp::routes())
        .merge(messages::routes())
        .merge(sessions::routes())
        .merge(wiki::routes())
}
