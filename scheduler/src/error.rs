use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::json;
use thiserror::Error;

/// Scheduler error types with contextual wrapping
#[derive(Debug, Error)]
pub enum SchedulerError {
    #[error("database error: {0}")]
    Database(String),

    #[error("resource not found: {0}")]
    NotFound(String),

    #[error("validation failed: {0}")]
    ValidationFailed(String),

    #[error("conflict: {0}")]
    Conflict(String),

    #[error("unauthorized: {0}")]
    Unauthorized(String),

    #[error("forbidden: {0}")]
    Forbidden(String),

    #[error("search failed: {0}")]
    SearchFailed(String),
}

impl IntoResponse for SchedulerError {
    fn into_response(self) -> Response {
        match self {
            SchedulerError::NotFound(msg) => {
                (StatusCode::NOT_FOUND, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::ValidationFailed(msg) => {
                (StatusCode::BAD_REQUEST, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::Database(msg) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": msg})),
            )
                .into_response(),
            SchedulerError::Conflict(msg) => {
                (StatusCode::CONFLICT, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::Unauthorized(msg) => (
                StatusCode::UNAUTHORIZED,
                [("www-authenticate", "Bearer")],
                Json(json!({"error": msg})),
            )
                .into_response(),
            SchedulerError::Forbidden(msg) => {
                (StatusCode::FORBIDDEN, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::SearchFailed(msg) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": msg})),
            )
                .into_response(),
        }
    }
}
