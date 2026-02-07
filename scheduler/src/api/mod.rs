use axum::Router;

use crate::app::AppState;

pub mod agent_card;
pub mod config;
pub mod executions;
pub mod handlers;
pub mod health;
pub mod jsonrpc;
pub mod worker;

/// Build API router with all endpoint modules
pub fn routes() -> Router<AppState> {
    Router::new()
        .merge(health::routes())
        .merge(executions::routes())
        .merge(config::routes())
        .merge(jsonrpc::routes())
        .merge(agent_card::routes())
        .merge(worker::routes())
}
