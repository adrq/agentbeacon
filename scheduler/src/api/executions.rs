use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tracing::warn;

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
    pub root_agent_id: String,
    pub agent_ids: Vec<String>,
    pub parts: Vec<serde_json::Value>,
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
    // Validate agent_ids non-empty and root_agent_id ∈ agent_ids
    if req.agent_ids.is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "agent_ids must be non-empty".to_string(),
        ));
    }
    if !req.agent_ids.contains(&req.root_agent_id) {
        return Err(SchedulerError::ValidationFailed(
            "root_agent_id must be in agent_ids".to_string(),
        ));
    }

    // Validate parts non-empty and at least one text part with non-empty text
    if req.parts.is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "parts must be non-empty".to_string(),
        ));
    }
    if !crate::services::messaging::has_deliverable_content(&req.parts) {
        return Err(SchedulerError::ValidationFailed(
            "parts must contain at least one non-empty text part or file part with bytes"
                .to_string(),
        ));
    }

    // Deduplicate
    let mut seen = std::collections::HashSet::new();
    let all_agent_ids: Vec<String> = req
        .agent_ids
        .iter()
        .filter(|id| seen.insert((*id).clone()))
        .cloned()
        .collect();
    let lead_agent_id = req.root_agent_id.clone();

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
        &req.parts,
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
    // Pre-read for fast rejection (not authoritative — just avoids unnecessary CAS)
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;
    if matches!(exec.status.as_str(), "completed" | "failed" | "canceled") {
        return Err(SchedulerError::Conflict(format!(
            "execution is already in terminal state: {}",
            exec.status
        )));
    }

    // CAS-first: establish this handler as the winner before touching sessions.
    use db::executions::CasResult;
    match db::executions::update_status_cas(
        &state.db_pool,
        &id,
        "canceled",
        &["submitted", "working", "input-required"],
    )
    .await?
    {
        CasResult::Applied => {}
        CasResult::Conflict => {
            let current = db::executions::get_by_id(&state.db_pool, &id).await?;
            return Err(SchedulerError::Conflict(format!(
                "execution transitioned to '{}' by concurrent handler",
                current.status
            )));
        }
        CasResult::NotFound => {
            return Err(SchedulerError::NotFound(format!(
                "execution not found: {id}"
            )));
        }
    }

    // --- Post-CAS best-effort: execution is irrevocably terminal. ---
    // Cascade and sweep failures are logged but do NOT fail the request.

    use crate::services::cascade::{CascadeMode, terminate_subtree};

    match db::sessions::list_by_execution(&state.db_pool, &id).await {
        Ok(sessions) => {
            if let Some(root) = sessions.iter().find(|s| s.parent_session_id.is_none())
                && let Err(e) = terminate_subtree(
                    &state.db_pool,
                    &root.id,
                    true,
                    CascadeMode::Cancel,
                    &state.event_broadcast,
                    &state.task_queue,
                )
                .await
            {
                tracing::warn!(execution_id = %id, error = %e, "post-CAS cascade failed");
            }
        }
        Err(e) => {
            tracing::warn!(execution_id = %id, error = %e, "post-CAS session list for cascade failed");
        }
    }

    // Safety sweep: cancel any non-terminal sessions.
    // SQL guard (AND status NOT IN terminal) prevents overwriting sessions
    // that became terminal between list read and update.
    let sweep_sql = state.db_pool.prepare_query(
        "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP, \
         completed_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND status NOT IN ('completed', 'failed', 'canceled')",
    );
    match db::sessions::list_by_execution(&state.db_pool, &id).await {
        Ok(remaining) => {
            for session in &remaining {
                if !matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
                    match sqlx::query(&sweep_sql)
                        .bind("canceled")
                        .bind(&session.id)
                        .execute(state.db_pool.as_ref())
                        .await
                    {
                        Err(e) => {
                            tracing::warn!(session_id = %session.id, error = %e, "post-CAS sweep failed");
                        }
                        Ok(result) if result.rows_affected() > 0 => {
                            let sweep_event = json!({"from": session.status, "to": "canceled"});
                            let _ = db::events::insert(
                                &state.db_pool,
                                &id,
                                Some(&session.id),
                                "state_change",
                                &serde_json::to_string(&sweep_event).unwrap(),
                            )
                            .await
                            .inspect_err(|e| {
                                tracing::warn!(session_id = %session.id, error = %e, "post-CAS sweep event insert failed");
                            })
                            .ok()
                            .map(|event_id| {
                                let _ = state
                                    .event_broadcast
                                    .send(EventNotification::persisted(id.clone(), event_id));
                            });
                        }
                        Ok(_) => {} // SQL guard blocked write — session already terminal
                    }
                }
            }
        }
        Err(e) => {
            tracing::warn!(execution_id = %id, error = %e, "post-CAS session list for sweep failed");
        }
    }

    state.task_queue.wake_waiters();

    // Record execution state_change event (best-effort)
    let exec_state_event = json!({"from": exec.status, "to": "canceled"});
    let _ = db::events::insert(
        &state.db_pool,
        &id,
        None,
        "state_change",
        &serde_json::to_string(&exec_state_event).unwrap(),
    )
    .await
    .inspect_err(|e| {
        tracing::warn!(execution_id = %id, error = %e, "post-CAS event insert failed");
    })
    .ok()
    .map(|event_id| {
        let _ = state
            .event_broadcast
            .send(EventNotification::persisted(id.clone(), event_id));
    });

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
    // Pre-read for fast rejection
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;
    if !matches!(exec.status.as_str(), "working" | "input-required") {
        return Err(SchedulerError::Conflict(format!(
            "execution must be 'working' or 'input-required' to complete (current: {})",
            exec.status
        )));
    }

    // CAS-first: establish this handler as the winner before touching sessions.
    use db::executions::CasResult;
    match db::executions::update_status_cas(
        &state.db_pool,
        &id,
        "completed",
        &["working", "input-required"],
    )
    .await?
    {
        CasResult::Applied => {}
        CasResult::Conflict => {
            let current = db::executions::get_by_id(&state.db_pool, &id).await?;
            return Err(SchedulerError::Conflict(format!(
                "execution transitioned to '{}' by concurrent handler",
                current.status
            )));
        }
        CasResult::NotFound => {
            return Err(SchedulerError::NotFound(format!(
                "execution not found: {id}"
            )));
        }
    }

    // --- Post-CAS best-effort: execution is irrevocably terminal. ---
    // Release transitions: input-required → completed, working/submitted → canceled.
    use crate::services::cascade::{CascadeMode, terminate_subtree};

    match db::sessions::list_by_execution(&state.db_pool, &id).await {
        Ok(sessions) => {
            if let Some(root) = sessions.iter().find(|s| s.parent_session_id.is_none())
                && let Err(e) = terminate_subtree(
                    &state.db_pool,
                    &root.id,
                    true,
                    CascadeMode::Release,
                    &state.event_broadcast,
                    &state.task_queue,
                )
                .await
            {
                tracing::warn!(execution_id = %id, error = %e, "post-CAS cascade failed");
            }
        }
        Err(e) => {
            tracing::warn!(execution_id = %id, error = %e, "post-CAS session list for cascade failed");
        }
    }

    // Safety sweep: Release-mode transitions for unreachable sessions.
    // SQL guard prevents TOCTOU overwrite of sessions that became terminal.
    match db::sessions::list_by_execution(&state.db_pool, &id).await {
        Ok(remaining) => {
            let sweep_sql = state.db_pool.prepare_query(
                "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP, \
                 completed_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND status NOT IN ('completed', 'failed', 'canceled')",
            );
            for session in &remaining {
                if !matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
                    let target = match session.status.as_str() {
                        "input-required" => "completed",
                        _ => "canceled",
                    };
                    match sqlx::query(&sweep_sql)
                        .bind(target)
                        .bind(&session.id)
                        .execute(state.db_pool.as_ref())
                        .await
                    {
                        Err(e) => {
                            tracing::warn!(session_id = %session.id, error = %e, "post-CAS sweep failed");
                        }
                        Ok(result) if result.rows_affected() > 0 => {
                            let sweep_event = json!({"from": session.status, "to": target});
                            let _ = db::events::insert(
                                &state.db_pool,
                                &id,
                                Some(&session.id),
                                "state_change",
                                &serde_json::to_string(&sweep_event).unwrap(),
                            )
                            .await
                            .inspect_err(|e| {
                                tracing::warn!(session_id = %session.id, error = %e, "post-CAS sweep event insert failed");
                            })
                            .ok()
                            .map(|event_id| {
                                let _ = state
                                    .event_broadcast
                                    .send(EventNotification::persisted(id.clone(), event_id));
                            });
                        }
                        Ok(_) => {} // SQL guard blocked write — session already terminal
                    }
                }
            }
        }
        Err(e) => {
            tracing::warn!(execution_id = %id, error = %e, "post-CAS session list for sweep failed");
        }
    }

    state.task_queue.wake_waiters();

    // Record execution state_change event (best-effort)
    let exec_state_event = json!({"from": exec.status, "to": "completed"});
    let _ = db::events::insert(
        &state.db_pool,
        &id,
        None,
        "state_change",
        &serde_json::to_string(&exec_state_event).unwrap(),
    )
    .await
    .inspect_err(|e| {
        tracing::warn!(execution_id = %id, error = %e, "post-CAS event insert failed");
    })
    .ok()
    .map(|event_id| {
        let _ = state
            .event_broadcast
            .send(EventNotification::persisted(id.clone(), event_id));
    });

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

/// Verify optional session auth matches the execution_id in the path.
/// User callers (no auth) pass through; session callers get 403 on mismatch.
async fn verify_execution_scope(
    state: &AppState,
    headers: &axum::http::HeaderMap,
    path_execution_id: &str,
) -> Result<(), SchedulerError> {
    if let Some(auth_header) = headers.get("authorization").and_then(|v| v.to_str().ok())
        && auth_header.len() > 7
        && auth_header[..7].eq_ignore_ascii_case("bearer ")
    {
        let token = &auth_header[7..];
        let session = db::sessions::get_by_id(&state.db_pool, token)
            .await
            .map_err(|_| SchedulerError::Unauthorized("invalid session token".to_string()))?;
        if session.execution_id != path_execution_id {
            return Err(SchedulerError::Forbidden(
                "session not authorized for this execution".to_string(),
            ));
        }
    }
    Ok(())
}

/// Get agent config pool for an execution (GET /api/executions/:id/agents)
///
/// Returns the configured agent pool (what can be delegated to), not running sessions.
async fn execution_agents_handler(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Vec<db::execution_agents::ExecutionAgentInfo>>, SchedulerError> {
    verify_execution_scope(&state, &headers, &id).await?;
    db::executions::get_by_id(&state.db_pool, &id).await?;
    let entries =
        db::execution_agents::list_agent_configs_for_execution(&state.db_pool, &id).await?;
    Ok(Json(entries))
}

#[derive(Debug, Deserialize)]
struct AddToPoolRequest {
    agent_id: String,
    #[serde(default = "default_true")]
    add_to_project: bool,
}

fn default_true() -> bool {
    true
}

/// Add agent to execution pool (POST /api/executions/:id/agents).
/// No session auth scoping — this is a user-only operation.
async fn add_to_execution_pool(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<AddToPoolRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;

    // Verify agent exists and is enabled
    let agent = db::agents::get_by_id(&state.db_pool, &req.agent_id).await?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent is disabled: {}",
            req.agent_id
        )));
    }

    db::execution_agents::insert(&state.db_pool, &id, &req.agent_id).await?;

    // Propagate to project pool if requested
    if req.add_to_project
        && let Some(ref project_id) = exec.project_id
        && let Err(e) = db::project_agents::insert(&state.db_pool, project_id, &req.agent_id).await
    {
        warn!(project_id, agent_id = %req.agent_id, error = %e, "failed to propagate agent to project pool");
    }

    Ok(StatusCode::NO_CONTENT)
}

/// Remove agent from execution pool (DELETE /api/executions/:id/agents/:agent_id).
/// No session auth scoping — this is a user-only operation.
async fn remove_from_execution_pool(
    State(state): State<AppState>,
    Path((id, agent_id)): Path<(String, String)>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::executions::get_by_id(&state.db_pool, &id).await?;
    let deleted = db::execution_agents::delete(&state.db_pool, &id, &agent_id).await?;
    if !deleted {
        return Err(SchedulerError::NotFound(format!(
            "agent {agent_id} not in execution pool"
        )));
    }
    Ok(StatusCode::NO_CONTENT)
}

/// Session discovery entry for the sessions endpoint
#[derive(Debug, Serialize)]
struct SessionDiscoveryResponse {
    session_id: String,
    hierarchical_name: String,
    agent_name: String,
    role: String,
    status: String,
    parent_name: Option<String>,
}

/// Get running sessions for an execution (GET /api/executions/:id/sessions)
async fn execution_sessions_handler(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Vec<SessionDiscoveryResponse>>, SchedulerError> {
    verify_execution_scope(&state, &headers, &id).await?;

    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;

    let name_tuples =
        crate::services::messaging::compute_hierarchical_names(&state.db_pool, &id).await?;
    let name_map: std::collections::HashMap<String, String> = name_tuples.into_iter().collect();

    let discovery = db::sessions::list_discovery_by_execution(&state.db_pool, &id).await?;

    let mut entries = Vec::new();
    for entry in &discovery {
        let role = if entry.parent_session_id.is_none() {
            "root-lead"
        } else if entry.depth >= exec.max_depth {
            "leaf"
        } else {
            "sub-lead"
        };

        let hier_name = name_map
            .get(&entry.session_id)
            .cloned()
            .unwrap_or_else(|| entry.slug.clone());

        let parent_name = entry
            .parent_session_id
            .as_ref()
            .and_then(|pid| name_map.get(pid).cloned());

        entries.push(SessionDiscoveryResponse {
            session_id: entry.session_id.clone(),
            hierarchical_name: hier_name,
            agent_name: entry.agent_name.clone(),
            role: role.to_string(),
            status: entry.status.clone(),
            parent_name,
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
        .route(
            "/api/executions/{id}/agents",
            get(execution_agents_handler).post(add_to_execution_pool),
        )
        .route(
            "/api/executions/{id}/agents/{agent_id}",
            axum::routing::delete(remove_from_execution_pool),
        )
        .route(
            "/api/executions/{id}/sessions",
            get(execution_sessions_handler),
        )
}
