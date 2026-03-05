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
    pub agent_id: Option<String>,
    pub agent_ids: Option<Vec<String>>,
    pub prompt: String,
    pub project_id: Option<String>,
    pub title: Option<String>,
    pub cwd: Option<String>,
    pub branch: Option<String>,
    pub context_id: Option<String>,
    pub max_depth: Option<i64>,
    pub max_width: Option<i64>,
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

/// Complete response
#[derive(Debug, Serialize)]
pub struct CompleteExecutionResponse {
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
    // Resolve lead agent_id: accept agent_id (singular) or agent_ids (array), not both
    let (lead_agent_id, all_agent_ids) = match (&req.agent_id, &req.agent_ids) {
        (Some(_), Some(_)) => {
            return Err(SchedulerError::ValidationFailed(
                "provide agent_id or agent_ids, not both".to_string(),
            ));
        }
        (None, None) => {
            return Err(SchedulerError::ValidationFailed(
                "agent_id or agent_ids is required".to_string(),
            ));
        }
        (Some(id), None) => (id.clone(), vec![id.clone()]),
        (None, Some(ids)) => {
            if ids.is_empty() {
                return Err(SchedulerError::ValidationFailed(
                    "agent_ids must be non-empty".to_string(),
                ));
            }
            let mut seen = std::collections::HashSet::new();
            let deduped: Vec<String> = ids
                .iter()
                .filter(|id| seen.insert((*id).clone()))
                .cloned()
                .collect();
            (ids[0].clone(), deduped)
        }
    };

    // Validate all agent IDs exist and are enabled
    for aid in &all_agent_ids {
        let agent = db::agents::get_by_id(&state.db_pool, aid)
            .await
            .map_err(|e| match e {
                SchedulerError::NotFound(_) => {
                    SchedulerError::ValidationFailed(format!("agent not found: {aid}"))
                }
                other => other,
            })?;
        if !agent.enabled {
            return Err(SchedulerError::ValidationFailed(format!(
                "agent is disabled: {aid}"
            )));
        }
    }

    let agent_id_refs: Vec<&str> = all_agent_ids.iter().map(|s| s.as_str()).collect();
    let result = execution::create_execution(
        &state.db_pool,
        &state.task_queue,
        &lead_agent_id,
        &agent_id_refs,
        &req.prompt,
        req.project_id.as_deref(),
        req.title.as_deref(),
        req.cwd.as_deref(),
        req.branch.as_deref(),
        req.context_id.as_deref(),
        req.max_depth,
        req.max_width,
    )
    .await?;

    // Broadcast for the initial "submitted" event created by the service.
    let _ = state
        .event_broadcast
        .send(EventNotification::persisted(result.execution.id.clone(), 0));

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

    // Terminate all sessions via cascade from root session
    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;
    // Find root session (no parent) and cascade from there
    if let Some(root) = sessions.iter().find(|s| s.parent_session_id.is_none()) {
        terminate_subtree(
            &state.db_pool,
            &root.id,
            true,
            CascadeMode::Cancel,
            &state.event_broadcast,
            &state.task_queue,
        )
        .await?;
    }

    // Safety sweep: cancel any sessions not reachable from the root tree.
    // Re-fetch to get current statuses after terminate_subtree.
    let remaining = db::sessions::list_by_execution(&state.db_pool, &id).await?;
    for session in &remaining {
        if !matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
            db::sessions::update_status(&state.db_pool, &session.id, "canceled").await?;
            let sweep_event = json!({"from": session.status, "to": "canceled"});
            let event_id = db::events::insert(
                &state.db_pool,
                &id,
                Some(&session.id),
                "state_change",
                &serde_json::to_string(&sweep_event).unwrap(),
            )
            .await?;
            let _ = state
                .event_broadcast
                .send(EventNotification::persisted(id.clone(), event_id));
        }
    }

    // Wake workers so they discover canceled sessions immediately
    state.task_queue.wake_waiters();

    // Cancel the execution itself
    db::executions::update_status(&state.db_pool, &id, "canceled").await?;

    let exec_state_event = json!({"from": exec.status, "to": "canceled"});
    let event_id = db::events::insert(
        &state.db_pool,
        &id,
        None,
        "state_change",
        &serde_json::to_string(&exec_state_event).unwrap(),
    )
    .await?;
    let _ = state
        .event_broadcast
        .send(EventNotification::persisted(id.clone(), event_id));

    let updated = db::executions::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(CancelExecutionResponse {
        execution: updated.into(),
    }))
}

/// Complete an execution (POST /api/executions/:id/complete)
async fn complete_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<CompleteExecutionResponse>, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;

    // Only accept from working or input-required (not submitted or terminal)
    if !matches!(exec.status.as_str(), "working" | "input-required") {
        return Err(SchedulerError::Conflict(format!(
            "execution must be 'working' or 'input-required' to complete (current: {})",
            exec.status
        )));
    }

    // Terminate all sessions via cascade from root session (Release mode).
    // Release transitions: input-required → completed, working/submitted → canceled.
    // A working root session becomes canceled (interrupted), which is correct —
    // the execution is what the user completed, the session was actively working.
    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;
    if let Some(root) = sessions.iter().find(|s| s.parent_session_id.is_none()) {
        terminate_subtree(
            &state.db_pool,
            &root.id,
            true,
            CascadeMode::Release,
            &state.event_broadcast,
            &state.task_queue,
        )
        .await?;
    }

    // Safety sweep: complete/cancel any sessions not reachable from the root tree
    let remaining = db::sessions::list_by_execution(&state.db_pool, &id).await?;
    for session in &remaining {
        if !matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
            let target = match session.status.as_str() {
                "input-required" => "completed",
                _ => "canceled",
            };
            db::sessions::update_status(&state.db_pool, &session.id, target).await?;
            let sweep_event = json!({"from": session.status, "to": target});
            let event_id = db::events::insert(
                &state.db_pool,
                &id,
                Some(&session.id),
                "state_change",
                &serde_json::to_string(&sweep_event).unwrap(),
            )
            .await?;
            let _ = state
                .event_broadcast
                .send(EventNotification::persisted(id.clone(), event_id));
        }
    }

    // Wake workers so they discover terminal states (after cascade + sweep)
    state.task_queue.wake_waiters();

    // Complete the execution
    db::executions::update_status(&state.db_pool, &id, "completed").await?;

    let exec_state_event = json!({"from": exec.status, "to": "completed"});
    let event_id = db::events::insert(
        &state.db_pool,
        &id,
        None,
        "state_change",
        &serde_json::to_string(&exec_state_event).unwrap(),
    )
    .await?;
    let _ = state
        .event_broadcast
        .send(EventNotification::persisted(id.clone(), event_id));

    let updated = db::executions::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(CompleteExecutionResponse {
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

/// Session-level discovery entry with hierarchical names
#[derive(Debug, Serialize)]
struct AgentDiscoveryEntry {
    name: String,
    agent_name: String,
    session_id: String,
    status: String,
    parent_name: Option<String>,
}

/// Get agents/sessions for an execution (GET /api/executions/:id/agents)
///
/// Returns session-level discovery with hierarchical names.
async fn execution_agents_handler(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<AgentDiscoveryEntry>>, SchedulerError> {
    db::executions::get_by_id(&state.db_pool, &id).await?;

    // TODO: compute_hierarchical_names loads sessions internally; we load them
    // again below for agent_id extraction. Acceptable at current scale (< 20 sessions).
    let name_tuples =
        crate::services::messaging::compute_hierarchical_names(&state.db_pool, &id).await?;
    let name_map: std::collections::HashMap<String, String> = name_tuples.into_iter().collect();

    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;

    let agent_ids: Vec<String> = sessions.iter().map(|s| s.agent_id.clone()).collect();
    let agent_names = db::agents::get_names_by_ids(&state.db_pool, &agent_ids).await?;

    let mut entries = Vec::new();
    for session in &sessions {
        let hier_name = name_map.get(&session.id).cloned().unwrap_or_default();
        let agent_name = agent_names
            .get(&session.agent_id)
            .cloned()
            .unwrap_or_default();

        entries.push(AgentDiscoveryEntry {
            name: hier_name,
            agent_name,
            session_id: session.id.clone(),
            status: session.status.clone(),
            parent_name: session
                .parent_session_id
                .as_ref()
                .and_then(|pid| name_map.get(pid).cloned()),
        });
    }

    Ok(Json(entries))
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
        .route(
            "/api/executions/{id}/complete",
            axum::routing::post(complete_execution),
        )
        .route("/api/executions/{id}/events", get(execution_events))
        .route("/api/executions/{id}/agents", get(execution_agents_handler))
}
