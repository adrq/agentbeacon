// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::json;
use thiserror::Error;

use crate::api::problem::Problem;

/// Scheduler error types with contextual wrapping
#[derive(Debug, Error)]
pub enum SchedulerError {
    /// An RFC 9457 problem raised by a call site that knows the exact code.
    #[error("{}", .0.code().as_str())]
    Problem(Box<Problem>),

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
            SchedulerError::Problem(problem) => (*problem).into_response(),
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
