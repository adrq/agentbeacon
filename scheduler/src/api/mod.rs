use axum::Router;

use crate::app::AppState;

pub mod config;
pub mod executions;
pub mod health;
pub mod workflows;

/// Build API router with all endpoint modules
pub fn routes() -> Router<AppState> {
    Router::new()
        .merge(health::routes())
        .merge(workflows::routes())
        .merge(executions::routes())
        .merge(config::routes())
}
