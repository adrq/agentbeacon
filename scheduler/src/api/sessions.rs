use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::Deserialize;
use serde_json::json;

use crate::api::types::{EventResponse, SessionResponse};
use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;

/// Query parameters for listing sessions
#[derive(Debug, Deserialize)]
pub struct ListSessionsQuery {
    pub status: Option<String>,
    pub execution_id: Option<String>,
}

/// Request body for posting a user message
#[derive(Debug, Deserialize)]
pub struct PostMessageRequest {
    pub message: String,
}

/// List sessions with optional filters (GET /api/sessions)
async fn list_sessions(
    State(state): State<AppState>,
    Query(query): Query<ListSessionsQuery>,
) -> Result<Json<Vec<SessionResponse>>, SchedulerError> {
    let sessions = db::sessions::list_filtered(
        &state.db_pool,
        query.status.as_deref(),
        query.execution_id.as_deref(),
    )
    .await?;

    Ok(Json(sessions.into_iter().map(Into::into).collect()))
}

/// Get events for a session (GET /api/sessions/{id}/events)
async fn session_events(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<EventResponse>>, SchedulerError> {
    // Verify session exists
    db::sessions::get_by_id(&state.db_pool, &id).await?;

    let events = db::events::list_by_session(&state.db_pool, &id).await?;
    Ok(Json(events.into_iter().map(Into::into).collect()))
}

/// Post a user message to a session (POST /api/sessions/{id}/message)
async fn post_message(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<PostMessageRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if session.status != "input-required" && session.status != "working" {
        return Err(SchedulerError::Conflict(format!(
            "session cannot accept messages (current status: {})",
            session.status
        )));
    }

    // Always: record user message event
    let msg_payload = json!({
        "role": "user",
        "parts": [{"kind": "text", "text": req.message}]
    });
    let event_id = db::events::insert(
        &state.db_pool,
        &session.execution_id,
        Some(&id),
        "message",
        &serde_json::to_string(&msg_payload).unwrap(),
    )
    .await?;
    let _ = state.event_broadcast.send(EventNotification {
        execution_id: session.execution_id.clone(),
        event_id,
    });

    // Always: push user message to session's inbox for delivery
    let message_payload = serde_json::Value::String(format!("[user]\n\n{}", req.message));
    state
        .task_queue
        .push(TaskAssignment {
            execution_id: session.execution_id.clone(),
            session_id: id.clone(),
            task_payload: message_payload,
        })
        .await?;

    // Always fetch execution for response
    let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;

    // Conditional: status transitions only for input-required → working
    let session_status;
    let execution_status;

    if session.status == "input-required" {
        db::sessions::update_status(&state.db_pool, &id, "working").await?;

        let session_state_event = json!({"from": "input-required", "to": "working"});
        let sc_event_id = db::events::insert(
            &state.db_pool,
            &session.execution_id,
            Some(&id),
            "state_change",
            &serde_json::to_string(&session_state_event).unwrap(),
        )
        .await?;
        let _ = state.event_broadcast.send(EventNotification {
            execution_id: session.execution_id.clone(),
            event_id: sc_event_id,
        });

        session_status = "working".to_string();

        // Only lead sessions propagate status changes to the execution
        if session.parent_session_id.is_none() {
            db::executions::update_status(&state.db_pool, &session.execution_id, "working").await?;

            let exec_state_event = json!({"from": execution.status, "to": "working"});
            let exec_event_id = db::events::insert(
                &state.db_pool,
                &session.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_state_event).unwrap(),
            )
            .await?;
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id: exec_event_id,
            });

            execution_status = "working".to_string();
        } else {
            execution_status = execution.status;
        }
    } else {
        // Already working — no transitions
        session_status = session.status;
        execution_status = execution.status;
    }

    Ok((
        StatusCode::OK,
        Json(json!({
            "event_id": event_id,
            "session_status": session_status,
            "execution_status": execution_status,
        })),
    ))
}

/// Session routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/sessions", get(list_sessions))
        .route("/api/sessions/{id}/events", get(session_events))
        .route(
            "/api/sessions/{id}/message",
            axum::routing::post(post_message),
        )
}
