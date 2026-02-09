use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;

/// Query parameters for listing sessions
#[derive(Debug, Deserialize)]
pub struct ListSessionsQuery {
    pub status: Option<String>,
    pub execution_id: Option<String>,
}

/// Session response
#[derive(Debug, Serialize)]
pub struct SessionResponse {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub agent_session_id: Option<String>,
    pub status: String,
    pub coordination_mode: String,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::sessions::Session> for SessionResponse {
    fn from(s: db::sessions::Session) -> Self {
        Self {
            id: s.id,
            execution_id: s.execution_id,
            parent_session_id: s.parent_session_id,
            agent_id: s.agent_id,
            agent_session_id: s.agent_session_id,
            status: s.status,
            coordination_mode: s.coordination_mode,
            created_at: s.created_at.to_rfc3339(),
            updated_at: s.updated_at.to_rfc3339(),
        }
    }
}

/// Event response
#[derive(Debug, Serialize)]
pub struct EventResponse {
    pub id: i64,
    pub event_type: String,
    pub payload: serde_json::Value,
    pub created_at: String,
}

impl From<db::events::Event> for EventResponse {
    fn from(e: db::events::Event) -> Self {
        // Parse payload from JSON string to Value for clean API output
        let payload_value = serde_json::from_str(&e.payload).unwrap_or(json!(e.payload));
        Self {
            id: e.id,
            event_type: e.event_type,
            payload: payload_value,
            created_at: e.created_at.to_rfc3339(),
        }
    }
}

/// Request body for posting a message/answer
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

/// Post a message/answer to a session (POST /api/sessions/{id}/message)
async fn post_message(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<PostMessageRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if session.status != "input-required" {
        return Err(SchedulerError::Conflict(format!(
            "session is not input-required (current status: {})",
            session.status
        )));
    }

    // Record user message event
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

    // Transition session to working
    db::sessions::update_status(&state.db_pool, &id, "working").await?;

    let session_state_event = json!({"from": "input-required", "to": "working"});
    db::events::insert(
        &state.db_pool,
        &session.execution_id,
        Some(&id),
        "state_change",
        &serde_json::to_string(&session_state_event).unwrap(),
    )
    .await?;

    // Only master sessions propagate status changes to the execution
    if session.parent_session_id.is_none() {
        let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;

        db::executions::update_status(&state.db_pool, &session.execution_id, "working").await?;

        let exec_state_event = json!({"from": execution.status, "to": "working"});
        db::events::insert(
            &state.db_pool,
            &session.execution_id,
            None,
            "state_change",
            &serde_json::to_string(&exec_state_event).unwrap(),
        )
        .await?;
    }

    // Push user answer to session's inbox for next_instruction delivery
    let answer_payload = json!({
        "kind": "user_answer",
        "message": req.message
    });
    state
        .task_queue
        .push(TaskAssignment {
            execution_id: session.execution_id.clone(),
            session_id: id.clone(),
            task_payload: answer_payload,
        })
        .await?;

    Ok((
        StatusCode::OK,
        Json(json!({"event_id": event_id, "status": "working"})),
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
