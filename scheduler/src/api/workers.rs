// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Admission of externally supervised workers.

use axum::{
    Json, Router,
    body::Bytes,
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::post,
};

use crate::api::problem::{Problem, ProblemCode, invalid};
use crate::app::AppState;

/// Admit an externally supervised worker (POST /api/v1/workers).
///
/// The scheduler mints the worker id and token; the body takes no fields.
async fn admit_worker(State(state): State<AppState>, body: Bytes) -> Response {
    if !body.iter().all(u8::is_ascii_whitespace) {
        match serde_json::from_slice::<serde_json::Value>(&body) {
            Ok(serde_json::Value::Object(map)) if map.is_empty() => {}
            Ok(serde_json::Value::Object(_)) => {
                return invalid("the request body takes no fields").into_response();
            }
            _ => return invalid("the request body must be a JSON object").into_response(),
        }
    }
    let worker_id = uuid::Uuid::new_v4().to_string();
    // The heartbeat exists before the registration is published.
    state
        .worker_heartbeats
        .write()
        .unwrap()
        .insert(worker_id.clone(), std::time::Instant::now());
    let token = match state.supervisor.admit_external(&worker_id).await {
        Ok(token) => token,
        Err(e) => {
            tracing::error!(error = %e, "worker admission failed");
            return Problem::new(ProblemCode::InternalError)
                .with_detail("the worker could not be admitted")
                .into_response();
        }
    };
    (
        StatusCode::CREATED,
        Json(serde_json::json!({ "worker_id": worker_id, "token": token })),
    )
        .into_response()
}

pub fn routes() -> Router<AppState> {
    Router::new().route("/workers", post(admit_worker))
}
