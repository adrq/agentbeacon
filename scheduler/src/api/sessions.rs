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
    let message_payload = json!({
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": req.message}]
        },
    });
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

/// Cancel a session and its subtree (POST /api/sessions/{id}/cancel)
async fn cancel_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
        return Err(SchedulerError::Conflict(format!(
            "session is already in terminal state: {}",
            session.status
        )));
    }

    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let result = terminate_subtree(
        &state.db_pool,
        &id,
        true, // include root
        CascadeMode::Cancel,
        &state.event_broadcast,
        &state.task_queue,
    )
    .await?;

    // Notify parent that this session was canceled
    notify_parent_of_termination(&state, &session, "canceled").await?;

    // Root session: propagate to execution status
    if session.parent_session_id.is_none() {
        let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;
        if !matches!(
            execution.status.as_str(),
            "completed" | "failed" | "canceled"
        ) {
            db::executions::update_status(&state.db_pool, &session.execution_id, "canceled")
                .await?;
            let exec_event = json!({"from": execution.status, "to": "canceled"});
            let event_id = db::events::insert(
                &state.db_pool,
                &session.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_event).unwrap(),
            )
            .await?;
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id,
            });
        }
    }

    Ok(Json(json!({
        "canceled": true,
        "sessions_terminated": result.sessions_terminated
    })))
}

/// Complete a session and its subtree (POST /api/sessions/{id}/complete)
async fn complete_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if session.status != "input-required" {
        return Err(SchedulerError::Conflict(format!(
            "session must be 'input-required' to complete (current: {})",
            session.status
        )));
    }

    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let result = terminate_subtree(
        &state.db_pool,
        &id,
        true,
        CascadeMode::Release,
        &state.event_broadcast,
        &state.task_queue,
    )
    .await?;

    // Notify parent that this session was completed
    notify_parent_of_termination(&state, &session, "completed").await?;

    // Root session: propagate to execution status
    if session.parent_session_id.is_none() {
        let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;
        if !matches!(
            execution.status.as_str(),
            "completed" | "failed" | "canceled"
        ) {
            db::executions::update_status(&state.db_pool, &session.execution_id, "completed")
                .await?;
            let exec_event = json!({"from": execution.status, "to": "completed"});
            let event_id = db::events::insert(
                &state.db_pool,
                &session.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_event).unwrap(),
            )
            .await?;
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id,
            });
        }
    }

    Ok(Json(json!({
        "completed": true,
        "sessions_terminated": result.sessions_terminated
    })))
}

/// Push a notification to the parent session's inbox when a child is
/// externally terminated (by user cancel/complete, not agent release).
async fn notify_parent_of_termination(
    state: &AppState,
    session: &db::sessions::Session,
    terminal_status: &str,
) -> Result<(), SchedulerError> {
    if let Some(ref parent_id) = session.parent_session_id {
        let agent = db::agents::get_by_id(&state.db_pool, &session.agent_id).await;
        let agent_name = agent
            .map(|a| a.name)
            .unwrap_or_else(|_| session.agent_id.clone());
        let agent_name = agent_name.replace(['\r', '\n'], " ");
        let agent_name = agent_name.trim();

        let formatted_text = format!(
            "[session {} ({}) was {} by user]\n\nThe child session has been terminated.",
            session.id, agent_name, terminal_status
        );
        let notification = json!({
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": formatted_text}]
            },
        });
        state
            .task_queue
            .push(TaskAssignment {
                execution_id: session.execution_id.clone(),
                session_id: parent_id.clone(),
                task_payload: notification,
            })
            .await?;
        state.task_queue.wake_waiters();
    }
    Ok(())
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
        .route(
            "/api/sessions/{id}/cancel",
            axum::routing::post(cancel_session),
        )
        .route(
            "/api/sessions/{id}/complete",
            axum::routing::post(complete_session),
        )
}
