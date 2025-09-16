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

    #[error("schema compilation failed: {0}")]
    SchemaCompilation(String),

    #[error("conflict: {0}")]
    Conflict(String),
}

impl IntoResponse for SchedulerError {
    fn into_response(self) -> Response {
        let (status, message) = match self {
            SchedulerError::WorkflowNotFound(_) => (StatusCode::NOT_FOUND, self.to_string()),
            SchedulerError::NotFound(_) => (StatusCode::NOT_FOUND, self.to_string()),
            SchedulerError::ValidationFailed(_) => (StatusCode::BAD_REQUEST, self.to_string()),
            SchedulerError::Database(_) => (StatusCode::INTERNAL_SERVER_ERROR, self.to_string()),
            SchedulerError::SchemaCompilation(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, self.to_string())
            }
            SchedulerError::Conflict(_) => (StatusCode::CONFLICT, self.to_string()),
        };

        (status, Json(json!({"error": message}))).into_response()
    }
}
