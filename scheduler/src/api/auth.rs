use axum::{
    Json,
    extract::FromRequestParts,
    http::{StatusCode, request::Parts},
    response::{IntoResponse, Response},
};
use serde_json::json;

use crate::app::AppState;
use crate::db;

#[derive(Debug, Clone, PartialEq)]
pub enum McpRole {
    RootLead,
    SubLead,
    Leaf,
}

pub struct McpSession {
    pub session_id: String,
    pub execution_id: String,
    pub agent_id: String,
    pub role: McpRole,
    pub depth: i64,
    pub max_depth: i64,
    pub max_width: i64,
    pub status: String,
    pub project_id: Option<String>,
}

/// Auth/session rejection per MCP spec:
/// - 401 for invalid/missing credentials (with WWW-Authenticate: Bearer)
/// - 404 for terminated sessions (spec: "MUST respond with HTTP 404 Not Found")
pub enum AuthRejection {
    Unauthorized(String),
    SessionTerminated(String),
    InternalError(String),
}

impl IntoResponse for AuthRejection {
    fn into_response(self) -> Response {
        match self {
            Self::Unauthorized(detail) => (
                StatusCode::UNAUTHORIZED,
                [("www-authenticate", "Bearer")],
                Json(json!({"error": "unauthorized", "detail": detail})),
            )
                .into_response(),
            Self::SessionTerminated(detail) => (
                StatusCode::NOT_FOUND,
                Json(json!({"error": "session_terminated", "detail": detail})),
            )
                .into_response(),
            Self::InternalError(detail) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal_error", "detail": detail})),
            )
                .into_response(),
        }
    }
}

impl FromRequestParts<AppState> for McpSession {
    type Rejection = AuthRejection;

    fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> impl std::future::Future<Output = Result<Self, Self::Rejection>> + Send {
        let db_pool = state.db_pool.clone();
        let auth_header = parts
            .headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());

        async move {
            let header = auth_header.ok_or_else(|| {
                AuthRejection::Unauthorized("missing Authorization header".into())
            })?;

            // RFC 7235: auth schemes are case-insensitive
            let token = if header.len() > 7 && header[..7].eq_ignore_ascii_case("bearer ") {
                &header[7..]
            } else {
                return Err(AuthRejection::Unauthorized(
                    "invalid Authorization format, expected Bearer <token>".into(),
                ));
            };

            let session = db::sessions::get_by_id(&db_pool, token)
                .await
                .map_err(|e| match e {
                    crate::error::SchedulerError::NotFound(_) => {
                        AuthRejection::Unauthorized("session not found".into())
                    }
                    other => AuthRejection::InternalError(other.to_string()),
                })?;

            // Terminated sessions → 404 per MCP spec, not 401
            if matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
                return Err(AuthRejection::SessionTerminated(
                    "session is in terminal state".into(),
                ));
            }

            let execution = db::executions::get_by_id(&db_pool, &session.execution_id)
                .await
                .map_err(|e| AuthRejection::InternalError(e.to_string()))?;

            let depth = db::sessions::compute_depth(&db_pool, &session.id, &session.execution_id)
                .await
                .map_err(|e| AuthRejection::InternalError(e.to_string()))?;

            let role = if session.parent_session_id.is_none() {
                McpRole::RootLead
            } else if depth >= execution.max_depth {
                McpRole::Leaf
            } else {
                McpRole::SubLead
            };

            Ok(McpSession {
                session_id: session.id,
                execution_id: session.execution_id,
                agent_id: session.agent_id,
                role,
                depth,
                max_depth: execution.max_depth,
                max_width: execution.max_width,
                status: session.status,
                project_id: execution.project_id.clone(),
            })
        }
    }
}
