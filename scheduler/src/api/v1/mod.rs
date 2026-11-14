// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::Router;
use tower_http::compression::CompressionLayer;

use crate::api::problem::{Problem, ProblemCode};
use crate::api::{
    agents, auth_routes, config, decisions, drivers, escalate, executions, mcp_servers, messages,
    projects, secrets, sessions, sse, wiki, wiki_tags, workers,
};
use crate::app::AppState;

/// Path prefix every versioned product route is served under.
pub const PREFIX: &str = "/api/v1";

async fn route_not_found() -> Problem {
    Problem::new(ProblemCode::ResourceNotFound).with_detail("no route matches this path")
}

/// The 405 problem of every versioned route.
pub(crate) fn method_not_allowed_problem() -> Problem {
    Problem::new(ProblemCode::MethodNotAllowed)
        .with_detail("this route does not support the request method")
}

async fn method_not_allowed() -> Problem {
    method_not_allowed_problem()
}

/// Build the versioned product router.
pub fn routes() -> Router<AppState> {
    let rest = Router::new()
        .merge(agents::routes())
        .merge(auth_routes::routes())
        .merge(config::routes())
        .merge(decisions::routes())
        .merge(drivers::routes())
        .merge(escalate::routes())
        .merge(executions::routes())
        .merge(mcp_servers::routes())
        .merge(messages::routes())
        .merge(projects::routes())
        .merge(secrets::routes())
        .merge(sessions::routes())
        .merge(wiki::routes())
        .merge(wiki_tags::routes())
        .merge(workers::routes())
        .layer(CompressionLayer::new());

    rest.merge(sse::routes())
        .fallback(route_not_found)
        .method_not_allowed_fallback(method_not_allowed)
}
