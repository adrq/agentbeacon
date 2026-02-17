use axum::Router;

use crate::app::AppState;

pub mod agent_card;
pub mod agents;
pub mod auth;
pub mod config;
pub mod executions;
pub mod handlers;
pub mod health;
pub mod jsonrpc;
pub mod mcp;
pub mod mcp_tools;
pub mod projects;
pub mod sessions;
pub mod types;
pub mod worker;

/// Build API router with all endpoint modules
pub fn routes() -> Router<AppState> {
    Router::new()
        .merge(health::routes())
        .merge(agents::routes())
        .merge(executions::routes())
        .merge(projects::routes())
        .merge(config::routes())
        .merge(jsonrpc::routes())
        .merge(agent_card::routes())
        .merge(worker::routes())
        .merge(mcp::routes())
        .merge(sessions::routes())
}
