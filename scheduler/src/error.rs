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

    #[error("workflow not found: {0}")]
    WorkflowNotFound(String),

    #[error("resource not found: {0}")]
    NotFound(String),

    #[error("validation failed: {0}")]
    ValidationFailed(String),

    #[error("DAG validation failed")]
    DagValidationFailed(Vec<String>),

    #[error("schema compilation failed: {0}")]
    SchemaCompilation(String),

    #[error("conflict: {0}")]
    Conflict(String),
}

impl IntoResponse for SchedulerError {
    fn into_response(self) -> Response {
        match self {
            SchedulerError::WorkflowNotFound(msg) => {
                (StatusCode::NOT_FOUND, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::NotFound(msg) => {
                (StatusCode::NOT_FOUND, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::ValidationFailed(msg) => {
                (StatusCode::BAD_REQUEST, Json(json!({"error": msg}))).into_response()
            }
            SchedulerError::DagValidationFailed(issues) => {
                // Return HTTP 422 with {"status": "error", "issues": [...]} per spec
                (
                    StatusCode::UNPROCESSABLE_ENTITY,
                    Json(json!({"status": "error", "issues": issues})),
                )
                    .into_response()
            }
            SchedulerError::Database(msg) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": msg})),
            )
                .into_response(),
            SchedulerError::SchemaCompilation(msg) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": msg})),
            )
                .into_response(),
            SchedulerError::Conflict(msg) => {
                (StatusCode::CONFLICT, Json(json!({"error": msg}))).into_response()
            }
        }
    }
}
