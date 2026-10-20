// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Route classification and the middleware that enforces it.
//!
//! Every route is listed in [`ROUTES`] with the kind of principal it admits.
//! A matched route that is not listed admits no principal.

use axum::{
    extract::{FromRequestParts, MatchedPath, RawPathParams, Request, State},
    http::{HeaderMap, HeaderValue, Method, StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
};

use crate::api::problem::{Problem, ProblemCode};
use crate::app::AppState;
use crate::services::auth::principal::{self, AgentPrincipal, Principal, ResolveError};

/// Detail of every 401 answered for a missing or unusable credential.
pub const UNAUTHORIZED_DETAIL: &str = "a valid credential is required";
/// Detail of every 403 answered for a credential that may not do this.
pub const FORBIDDEN_DETAIL: &str = "this credential is not permitted to do this";

/// Which agents a human-or-agent route admits.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum AgentScope {
    /// No path or query rule; the handler applies its own rules.
    Unscoped,
    /// The `{id}` or `{execution_id}` path parameter is the agent's execution.
    ExecutionPath,
    /// The `{id}` path parameter names a session in the agent's execution.
    SessionPath,
    /// The `session_id` query parameter names a session in the agent's execution.
    SessionQuery,
    /// The `execution_id` query parameter is the agent's execution.
    ExecutionQuery,
}

/// The principals a route admits.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Access {
    Public,
    Human,
    Agent,
    HumanOrAgent(AgentScope),
    Worker,
}

use Access::*;
use AgentScope::*;

/// Every route: method (`*` for any), matched path template, and access.
pub const ROUTES: &[(&str, &str, Access)] = &[
    // Operational probes, discovery and static assets.
    ("GET", "/api/health", Public),
    ("GET", "/api/ready", Public),
    ("GET", "/api/versions", Public),
    ("GET", "/api/docs", Public),
    ("GET", "/.well-known/agent-card.json", Public),
    ("*", "/api", Public),
    ("*", "/api/", Public),
    ("*", "/api/{*rest}", Public),
    ("GET", "/", Public),
    ("GET", "/index.html", Public),
    ("GET", "/docs", Public),
    ("GET", "/docs/", Public),
    ("GET", "/docs/{*path}", Public),
    ("POST", "/api/v1/auth/login", Public),
    // Worker transport.
    ("POST", "/api/worker/sync", Worker),
    ("POST", "/api/worker/events", Worker),
    ("POST", "/api/worker/token", Worker),
    // Agent-only.
    ("POST", "/mcp", Agent),
    ("GET", "/mcp", Agent),
    ("DELETE", "/mcp", Agent),
    ("POST", "/api/v1/messages", Agent),
    ("POST", "/api/v1/escalate", Agent),
    // Reads within the agent's own execution.
    (
        "GET",
        "/api/v1/executions/{id}",
        HumanOrAgent(ExecutionPath),
    ),
    (
        "GET",
        "/api/v1/executions/{id}/events",
        HumanOrAgent(ExecutionPath),
    ),
    (
        "GET",
        "/api/v1/executions/{execution_id}/events/{event_id}",
        HumanOrAgent(ExecutionPath),
    ),
    (
        "GET",
        "/api/v1/executions/{id}/events/stream",
        HumanOrAgent(ExecutionPath),
    ),
    (
        "GET",
        "/api/v1/executions/{id}/agents",
        HumanOrAgent(ExecutionPath),
    ),
    (
        "GET",
        "/api/v1/executions/{id}/sessions",
        HumanOrAgent(ExecutionPath),
    ),
    ("GET", "/api/v1/sessions/{id}", HumanOrAgent(SessionPath)),
    (
        "GET",
        "/api/v1/sessions/{id}/events",
        HumanOrAgent(SessionPath),
    ),
    (
        "GET",
        "/api/v1/sessions/{id}/worktree",
        HumanOrAgent(SessionPath),
    ),
    (
        "GET",
        "/api/v1/sessions/{id}/worktree/diff",
        HumanOrAgent(SessionPath),
    ),
    (
        "GET",
        "/api/v1/sessions/{id}/worktree/branches",
        HumanOrAgent(SessionPath),
    ),
    ("GET", "/api/v1/messages", HumanOrAgent(SessionQuery)),
    ("GET", "/api/v1/sessions", HumanOrAgent(ExecutionQuery)),
    // The wiki: the handlers apply the sharing rules.
    ("GET", "/api/v1/wiki/search", HumanOrAgent(Unscoped)),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/pages",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/pages/{slug}",
        HumanOrAgent(Unscoped),
    ),
    (
        "PUT",
        "/api/v1/projects/{project_id}/wiki/pages/{slug}",
        HumanOrAgent(Unscoped),
    ),
    (
        "PATCH",
        "/api/v1/projects/{project_id}/wiki/pages/{slug}",
        HumanOrAgent(Unscoped),
    ),
    (
        "DELETE",
        "/api/v1/projects/{project_id}/wiki/pages/{slug}",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/pages/{slug}/revisions",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/pages/{slug}/revisions/{rev}",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/tags",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/subscriptions",
        HumanOrAgent(Unscoped),
    ),
    (
        "POST",
        "/api/v1/projects/{project_id}/wiki/subscriptions",
        HumanOrAgent(Unscoped),
    ),
    (
        "DELETE",
        "/api/v1/projects/{project_id}/wiki/subscriptions/{sub_id}",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/changes",
        HumanOrAgent(Unscoped),
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/wiki/export",
        HumanOrAgent(Unscoped),
    ),
    // Human-only: executions and sessions.
    ("GET", "/api/v1/executions", Human),
    ("POST", "/api/v1/executions", Human),
    ("PATCH", "/api/v1/executions/{id}", Human),
    ("POST", "/api/v1/executions/{id}/terminate", Human),
    ("POST", "/api/v1/executions/{id}/agents", Human),
    ("DELETE", "/api/v1/executions/{id}/agents/{agent_id}", Human),
    ("DELETE", "/api/v1/sessions/{id}/worktree", Human),
    ("POST", "/api/v1/sessions/{id}/message", Human),
    ("POST", "/api/v1/sessions/{id}/terminate", Human),
    ("POST", "/api/v1/sessions/{id}/stop", Human),
    ("POST", "/api/v1/sessions/{id}/continue", Human),
    ("POST", "/api/v1/sessions/{id}/recover", Human),
    // Human-only: decisions.
    ("GET", "/api/v1/decisions", Human),
    ("GET", "/api/v1/decisions/{event_id}", Human),
    ("POST", "/api/v1/escalate/{event_id}/dismiss", Human),
    // Human-only: configuration.
    ("GET", "/api/v1/agents", Human),
    ("POST", "/api/v1/agents", Human),
    ("GET", "/api/v1/agents/{id}", Human),
    ("PATCH", "/api/v1/agents/{id}", Human),
    ("DELETE", "/api/v1/agents/{id}", Human),
    ("GET", "/api/v1/config", Human),
    ("POST", "/api/v1/config", Human),
    ("GET", "/api/v1/drivers", Human),
    ("POST", "/api/v1/drivers", Human),
    ("GET", "/api/v1/drivers/{id}", Human),
    ("PATCH", "/api/v1/drivers/{id}", Human),
    ("DELETE", "/api/v1/drivers/{id}", Human),
    ("GET", "/api/v1/drivers/{platform}/descriptor", Human),
    ("GET", "/api/v1/drivers/{platform}/models", Human),
    ("GET", "/api/v1/mcp-servers", Human),
    ("POST", "/api/v1/mcp-servers", Human),
    ("GET", "/api/v1/mcp-servers/{id}", Human),
    ("PATCH", "/api/v1/mcp-servers/{id}", Human),
    ("DELETE", "/api/v1/mcp-servers/{id}", Human),
    // Human-only: projects.
    ("GET", "/api/v1/projects", Human),
    ("POST", "/api/v1/projects", Human),
    ("GET", "/api/v1/projects/{id}", Human),
    ("PATCH", "/api/v1/projects/{id}", Human),
    ("DELETE", "/api/v1/projects/{id}", Human),
    ("GET", "/api/v1/projects/{id}/agents", Human),
    ("POST", "/api/v1/projects/{id}/agents", Human),
    ("DELETE", "/api/v1/projects/{id}/agents/{agent_id}", Human),
    ("GET", "/api/v1/projects/{id}/mcp-servers", Human),
    ("POST", "/api/v1/projects/{id}/mcp-servers", Human),
    (
        "DELETE",
        "/api/v1/projects/{id}/mcp-servers/{mcp_server_id}",
        Human,
    ),
    // Human-only: wiki share-tag administration.
    ("GET", "/api/v1/wiki/tags", Human),
    ("POST", "/api/v1/wiki/tags/{tag_id}/members", Human),
    (
        "PATCH",
        "/api/v1/wiki/tags/{tag_id}/members/{project}",
        Human,
    ),
    (
        "DELETE",
        "/api/v1/wiki/tags/{tag_id}/members/{project}",
        Human,
    ),
    // Human-only: logins and workers.
    ("GET", "/api/v1/auth/logins", Human),
    ("DELETE", "/api/v1/auth/logins/{id}", Human),
    ("POST", "/api/v1/auth/logout", Human),
    ("POST", "/api/v1/workers", Human),
    // Human-only: A2A JSON-RPC.
    ("POST", "/rpc", Human),
];

/// The access a route admits, if it is listed.
pub fn access_for(method: &Method, path: &str) -> Option<Access> {
    let method = if method == Method::HEAD {
        "GET"
    } else {
        method.as_str()
    };
    ROUTES
        .iter()
        .find(|(m, p, _)| *p == path && (*m == "*" || *m == method))
        .map(|(_, _, access)| *access)
}

/// Whether any method of this path is listed.
fn path_is_listed(path: &str) -> bool {
    ROUTES.iter().any(|(_, p, _)| *p == path)
}

/// 405 for a method no entry lists on a listed path, in the router's own
/// form, with `Allow` naming the listed methods. No handler runs.
fn method_not_allowed(path: &str) -> Response {
    let allow = ROUTES
        .iter()
        .filter(|(_, p, _)| *p == path)
        .flat_map(|(m, _, _)| match *m {
            "GET" => vec!["GET", "HEAD"],
            m => vec![m],
        })
        .collect::<Vec<_>>()
        .join(",");
    let in_v1 = path
        .strip_prefix(crate::api::v1::PREFIX)
        .is_some_and(|rest| rest.is_empty() || rest.starts_with('/'));
    let mut response = if in_v1 {
        crate::api::v1::method_not_allowed_problem().into_response()
    } else {
        StatusCode::METHOD_NOT_ALLOWED.into_response()
    };
    if let Ok(value) = HeaderValue::from_str(&allow) {
        response.headers_mut().insert(header::ALLOW, value);
    }
    response
}

/// 401 for a missing or unusable credential.
pub fn unauthorized() -> Response {
    unauthorized_with(UNAUTHORIZED_DETAIL)
}

/// 401 with a specific detail.
pub fn unauthorized_with(detail: &str) -> Response {
    let mut response = Problem::new(ProblemCode::AuthUnauthorized)
        .with_detail(detail)
        .into_response();
    response
        .headers_mut()
        .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
    response
}

/// 403 for a credential that may not do this.
pub fn forbidden() -> Response {
    Problem::new(ProblemCode::AuthForbidden)
        .with_detail(FORBIDDEN_DETAIL)
        .into_response()
}

fn internal(e: crate::error::SchedulerError) -> Response {
    e.into_response()
}

/// Authenticate the caller, check the route's access, and attach the principal.
pub async fn authorize(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let Some(path) = request
        .extensions()
        .get::<MatchedPath>()
        .map(|m| m.as_str().to_string())
    else {
        return next.run(request).await;
    };
    let access = match access_for(request.method(), &path) {
        Some(access) => Some(access),
        None if path_is_listed(&path) => return method_not_allowed(&path),
        None => None,
    };
    if access == Some(Public) {
        return next.run(request).await;
    }

    let presented = principal::presented(request.headers(), &state.login_cookie_name);
    let principal = match principal::resolve(&state.db_pool, &state.supervisor, &presented).await {
        Ok(Some(principal)) => principal,
        Ok(None) | Err(ResolveError::Unauthenticated) => return unauthorized(),
        Err(ResolveError::Internal(e)) => return internal(e),
    };

    let Some(access) = access else {
        return forbidden();
    };
    let (mut parts, body) = request.into_parts();
    match check_access(&state, access, &principal, &mut parts).await {
        Ok(true) => {}
        Ok(false) => return forbidden(),
        Err(response) => return response,
    }
    if let Principal::Human {
        via_cookie: true, ..
    } = &principal
        && !matches!(parts.method, Method::GET | Method::HEAD | Method::OPTIONS)
        && !origin_allowed(&state, &parts.headers)
    {
        return forbidden();
    }

    parts.extensions.insert(principal);
    next.run(Request::from_parts(parts, body)).await
}

async fn check_access(
    state: &AppState,
    access: Access,
    principal: &Principal,
    parts: &mut axum::http::request::Parts,
) -> Result<bool, Response> {
    match (access, principal) {
        (Public, _) => Ok(true),
        (Human, Principal::Human { .. }) => Ok(true),
        (Agent, Principal::Agent(_)) => Ok(true),
        (Worker, Principal::Worker { .. }) => Ok(true),
        (HumanOrAgent(_), Principal::Human { .. }) => Ok(true),
        (HumanOrAgent(scope), Principal::Agent(agent)) => {
            agent_in_scope(state, scope, agent, parts).await
        }
        _ => Ok(false),
    }
}

async fn agent_in_scope(
    state: &AppState,
    scope: AgentScope,
    agent: &AgentPrincipal,
    parts: &mut axum::http::request::Parts,
) -> Result<bool, Response> {
    match scope {
        Unscoped => Ok(true),
        ExecutionPath => {
            let params = path_params(state, parts).await?;
            let execution = params
                .iter()
                .find(|(k, _)| k == "execution_id" || k == "id")
                .map(|(_, v)| v.as_str());
            Ok(execution == Some(agent.execution_id.as_str()))
        }
        SessionPath => {
            let params = path_params(state, parts).await?;
            let Some((_, session_id)) = params.iter().find(|(k, _)| k == "id") else {
                return Ok(false);
            };
            principal::session_in_execution(&state.db_pool, session_id, &agent.execution_id)
                .await
                .map_err(internal)
        }
        SessionQuery => {
            let Some(session_id) = query_param(parts, "session_id") else {
                return Ok(false);
            };
            principal::session_in_execution(&state.db_pool, &session_id, &agent.execution_id)
                .await
                .map_err(internal)
        }
        ExecutionQuery => {
            Ok(query_param(parts, "execution_id").as_deref() == Some(agent.execution_id.as_str()))
        }
    }
}

async fn path_params(
    state: &AppState,
    parts: &mut axum::http::request::Parts,
) -> Result<Vec<(String, String)>, Response> {
    let params = RawPathParams::from_request_parts(parts, state)
        .await
        .map_err(|_| forbidden())?;
    Ok(params
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect())
}

fn query_param(parts: &axum::http::request::Parts, name: &str) -> Option<String> {
    let axum::extract::Query(params) =
        axum::extract::Query::<std::collections::HashMap<String, String>>::try_from_uri(&parts.uri)
            .ok()?;
    params.get(name).cloned()
}

/// Whether the request's `Origin` is its own origin or an allowed one.
fn origin_allowed(state: &AppState, headers: &HeaderMap) -> bool {
    let Some(origin) = headers
        .get(header::ORIGIN)
        .and_then(|v| v.to_str().ok())
        .map(|o| o.trim_end_matches('/'))
    else {
        return false;
    };
    if let Some(host) = headers.get(header::HOST).and_then(|v| v.to_str().ok())
        && (origin == format!("http://{host}") || origin == format!("https://{host}"))
    {
        return true;
    }
    if origin == state.resolve_base_url(headers).trim_end_matches('/') {
        return true;
    }
    state
        .allowed_origins
        .iter()
        .any(|allowed| allowed.trim_end_matches('/') == origin)
}

/// The authenticated principal of a request, attached by [`authorize`].
pub struct Authenticated(pub Principal);

impl<S: Send + Sync> FromRequestParts<S> for Authenticated {
    type Rejection = Response;

    async fn from_request_parts(
        parts: &mut axum::http::request::Parts,
        _state: &S,
    ) -> Result<Self, Self::Rejection> {
        parts
            .extensions
            .get::<Principal>()
            .cloned()
            .map(Authenticated)
            .ok_or_else(|| (StatusCode::INTERNAL_SERVER_ERROR, "no principal").into_response())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::queue::TaskQueue;
    use crate::search::WikiSearchIndex;
    use crate::supervisor::Supervisor;
    use axum::{Router, body::Body, routing::get};
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use tower::ServiceExt;

    async fn test_state() -> AppState {
        sqlx::any::install_default_drivers();
        let pool = crate::db::pool::create("sqlite::memory:")
            .await
            .expect("test pool");
        AppState::new(
            pool.clone(),
            Arc::new(TaskQueue::new(pool)),
            "http://localhost:1".to_string(),
            None,
            1,
            tokio::sync::broadcast::channel(1).0,
            WikiSearchIndex::new(std::env::temp_dir()),
            Arc::new(Supervisor::new(
                1,
                None,
                Some(0),
                std::time::Duration::from_secs(1),
                None,
            )),
        )
    }

    #[tokio::test]
    async fn an_unlisted_method_on_a_listed_path_reaches_no_handler() {
        assert_eq!(access_for(&Method::GET, "/api/health"), Some(Public));
        assert_eq!(access_for(&Method::POST, "/api/health"), None);

        let ran = Arc::new(AtomicBool::new(false));
        let flag = ran.clone();
        let state = test_state().await;
        let app = Router::new()
            .route(
                "/api/health",
                get(|| async { "ok" }).post(move || {
                    flag.store(true, Ordering::SeqCst);
                    async { "ran" }
                }),
            )
            .route_layer(axum::middleware::from_fn_with_state(
                state.clone(),
                authorize,
            ))
            .with_state(state);

        let post = app
            .clone()
            .oneshot(
                Request::post("/api/health")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        assert_eq!(post.status(), StatusCode::METHOD_NOT_ALLOWED);
        assert_eq!(post.headers()[header::ALLOW], "GET,HEAD");
        assert!(!ran.load(Ordering::SeqCst));

        let get = app
            .oneshot(
                Request::get("/api/health")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        assert_eq!(get.status(), StatusCode::OK);
    }
}
