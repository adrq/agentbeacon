// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, State},
    response::{IntoResponse, Response},
    routing::post,
};

use crate::api::authz::{Authenticated, forbidden, unauthorized};
use crate::api::handlers;
use crate::api::problem::{Problem, ProblemCode};
use crate::app::AppState;
use crate::db;
use crate::services::auth::mint_token;
use crate::services::auth::principal::Principal;

fn internal(e: impl std::fmt::Display) -> Response {
    tracing::error!(error = %e, "worker token rotation failed");
    Problem::new(ProblemCode::InternalError)
        .with_detail("the token could not be rotated")
        .into_response()
}

/// Replace the presented worker token with a new one (POST /api/worker/token).
async fn rotate_token(
    State(state): State<AppState>,
    Authenticated(principal): Authenticated,
) -> Response {
    let Principal::Worker {
        worker_id,
        credential_hash,
    } = principal
    else {
        return forbidden();
    };
    let pool = &state.db_pool;
    let mut tx = match pool.begin().await {
        Ok(tx) => tx,
        Err(e) => return internal(e),
    };
    match db::credentials::delete_in_tx(
        &mut tx,
        pool,
        &credential_hash,
        db::credentials::PRINCIPAL_WORKER,
    )
    .await
    {
        Ok(1) => {}
        Ok(_) => {
            let _ = tx.rollback().await;
            return unauthorized();
        }
        Err(e) => return internal(e),
    }
    let token = mint_token();
    if let Err(e) = db::credentials::insert_in_tx(
        &mut tx,
        pool,
        &token.hash,
        db::credentials::PRINCIPAL_WORKER,
        &worker_id,
        None,
        None,
    )
    .await
    {
        return internal(e);
    }
    if let Err(e) = tx.commit().await {
        return internal(e);
    }
    Json(serde_json::json!({ "token": token.plaintext })).into_response()
}

/// Create worker API router
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/worker/sync", post(handlers::worker_sync))
        .route("/api/worker/events", post(handlers::worker_event))
        .route("/api/worker/token", post(rotate_token))
        .layer(DefaultBodyLimit::max(256 * 1024 * 1024))
}
