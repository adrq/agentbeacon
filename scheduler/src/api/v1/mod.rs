// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::Router;
use tower_http::compression::CompressionLayer;

use crate::api::problem::{Problem, ProblemCode};
use crate::api::{
    agents, config, decisions, drivers, escalate, executions, mcp_servers, messages, projects,
    sessions, sse, wiki, wiki_tags,
};
use crate::app::AppState;

/// Path prefix every versioned product route is served under.
pub const PREFIX: &str = "/api/v1";

async fn route_not_found() -> Problem {
    Problem::new(ProblemCode::ResourceNotFound).with_detail("no route matches this path")
}

async fn method_not_allowed() -> Problem {
    Problem::new(ProblemCode::MethodNotAllowed)
        .with_detail("this route does not support the request method")
}

/// Build the versioned product router.
pub fn routes() -> Router<AppState> {
    let rest = Router::new()
        .merge(agents::routes())
        .merge(config::routes())
        .merge(decisions::routes())
        .merge(drivers::routes())
        .merge(escalate::routes())
        .merge(executions::routes())
        .merge(mcp_servers::routes())
        .merge(messages::routes())
        .merge(projects::routes())
        .merge(sessions::routes())
        .merge(wiki::routes())
        .merge(wiki_tags::routes())
        .layer(CompressionLayer::new());

    rest.merge(sse::routes())
        .fallback(route_not_found)
        .method_not_allowed_fallback(method_not_allowed)
}
