use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::api::types::{EventResponse, ExecutionResponse, SessionResponse};
use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;
use crate::services::execution;

/// Query parameters for listing executions
#[derive(Debug, Deserialize)]
pub struct ListExecutionsQuery {
    pub project_id: Option<String>,
    pub status: Option<String>,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
}

/// Execution detail with sessions
#[derive(Debug, Serialize)]
pub struct ExecutionDetailResponse {
    pub execution: ExecutionResponse,
    pub sessions: Vec<SessionResponse>,
}

/// Request body for creating an execution
#[derive(Debug, Deserialize)]
pub struct CreateExecutionRequest {
    pub agent_id: String,
    pub prompt: String,
    pub project_id: Option<String>,
    pub title: Option<String>,
    pub cwd: Option<String>,
    pub branch: Option<String>,
    pub context_id: Option<String>,
}

/// Response for create execution
#[derive(Debug, Serialize)]
pub struct CreateExecutionResponse {
    pub execution: ExecutionResponse,
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warning: Option<String>,
}

/// Cancel response
#[derive(Debug, Serialize)]
pub struct CancelExecutionResponse {
    pub execution: ExecutionResponse,
}

/// List all executions (GET /api/executions)
async fn list_executions(
    State(state): State<AppState>,
    Query(query): Query<ListExecutionsQuery>,
) -> Result<Json<Vec<ExecutionResponse>>, SchedulerError> {
    let executions = db::executions::list(
        &state.db_pool,
        query.project_id.as_deref(),
        query.status.as_deref(),
        query.limit,
        query.offset,
    )
    .await?;

    let responses: Vec<ExecutionResponse> = executions.into_iter().map(Into::into).collect();
    Ok(Json(responses))
}

/// Get execution by ID with sessions (GET /api/executions/:id)
async fn get_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<ExecutionDetailResponse>, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;
    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;

    Ok(Json(ExecutionDetailResponse {
        execution: exec.into(),
        sessions: sessions.into_iter().map(Into::into).collect(),
    }))
}

/// Create a new execution (POST /api/executions)
async fn create_execution_handler(
    State(state): State<AppState>,
    Json(req): Json<CreateExecutionRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let result = execution::create_execution(
        &state.db_pool,
        &state.task_queue,
        &req.agent_id,
        &req.prompt,
        req.project_id.as_deref(),
        req.title.as_deref(),
        req.cwd.as_deref(),
        req.branch.as_deref(),
        req.context_id.as_deref(),
    )
    .await?;

    // Broadcast for the initial "submitted" event created by the service.
    // We don't have the event_id, but sending event_id=0 triggers a DB backfill
    // on the SSE handler, which picks up the new event correctly.
    let _ = state.event_broadcast.send(EventNotification {
        execution_id: result.execution.id.clone(),
        event_id: 0,
    });

    Ok((
        StatusCode::CREATED,
        Json(CreateExecutionResponse {
            execution: result.execution.into(),
            session_id: result.session_id,
            warning: result.warning,
        }),
    ))
}

/// Cancel an execution (POST /api/executions/:id/cancel)
async fn cancel_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<CancelExecutionResponse>, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;

    // Check if already terminal
    if matches!(exec.status.as_str(), "completed" | "failed" | "canceled") {
        return Err(SchedulerError::Conflict(format!(
            "execution is already in terminal state: {}",
            exec.status
        )));
    }

    // Cancel all non-terminal sessions
    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;
    for session in &sessions {
        if !matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
            db::sessions::update_status(&state.db_pool, &session.id, "canceled").await?;

            let session_state_event = json!({"from": session.status, "to": "canceled"});
            let event_id = db::events::insert(
                &state.db_pool,
                &id,
                Some(&session.id),
                "state_change",
                &serde_json::to_string(&session_state_event).unwrap(),
            )
            .await?;
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: id.clone(),
                event_id,
            });
        }
    }

    // Cancel the execution itself
    db::executions::update_status(&state.db_pool, &id, "canceled").await?;

    // Wake long-polling workers so they discover the cancel immediately
    state.task_queue.wake_waiters();

    let exec_state_event = json!({"from": exec.status, "to": "canceled"});
    let event_id = db::events::insert(
        &state.db_pool,
        &id,
        None,
        "state_change",
        &serde_json::to_string(&exec_state_event).unwrap(),
    )
    .await?;
    let _ = state.event_broadcast.send(EventNotification {
        execution_id: id.clone(),
        event_id,
    });

    let updated = db::executions::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(CancelExecutionResponse {
        execution: updated.into(),
    }))
}

/// Get events for an execution (GET /api/executions/:id/events)
async fn execution_events(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<EventResponse>>, SchedulerError> {
    // Verify execution exists
    db::executions::get_by_id(&state.db_pool, &id).await?;

    let events = db::events::list_by_execution(&state.db_pool, &id).await?;
    Ok(Json(events.into_iter().map(Into::into).collect()))
}

/// Execution routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route(
            "/api/executions",
            get(list_executions).post(create_execution_handler),
        )
        .route("/api/executions/{id}", get(get_execution))
        .route(
            "/api/executions/{id}/cancel",
            axum::routing::post(cancel_execution),
        )
        .route("/api/executions/{id}/events", get(execution_events))
}
