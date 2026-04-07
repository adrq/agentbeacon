use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use tracing::warn;

use crate::api::types::{self, EventResponse, ExecutionResponse, SessionResponse};
use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;
use crate::services::{execution, reconciler, transition};

/// Query parameters for listing executions
#[derive(Debug, Deserialize)]
pub struct ListExecutionsQuery {
    pub project_id: Option<String>,
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

/// Derive execution display fields, fetching session data as needed.
async fn derive_execution_fields_async(
    pool: &db::DbPool,
    exec: &db::Execution,
) -> Result<types::ExecutionDerived, SchedulerError> {
    if let Some(ref outcome) = exec.outcome {
        return Ok(types::ExecutionDerived {
            status: outcome.clone(),
            completion_eligible: false,
        });
    }
    if exec.desired == "terminate" {
        return Ok(types::ExecutionDerived {
            status: "canceled".to_string(),
            completion_eligible: false,
        });
    }
    let snapshot = db::sessions::list_by_execution_with_pending(pool, &exec.id).await?;
    Ok(ExecutionResponse::derive_from_snapshot(exec, &snapshot))
}

/// List all executions (GET /api/executions)
async fn list_executions(
    State(state): State<AppState>,
    Query(query): Query<ListExecutionsQuery>,
) -> Result<Json<Vec<ExecutionResponse>>, SchedulerError> {
    let executions = db::executions::list(
        &state.db_pool,
        query.project_id.as_deref(),
        query.limit,
        query.offset,
    )
    .await?;

    let mut responses = Vec::with_capacity(executions.len());
    for exec in executions {
        let derived = derive_execution_fields_async(&state.db_pool, &exec).await?;
        let mut resp: ExecutionResponse = exec.into();
        resp.status = derived.status;
        resp.completion_eligible = derived.completion_eligible;
        responses.push(resp);
    }
    Ok(Json(responses))
}

/// Get execution by ID with sessions (GET /api/executions/:id)
async fn get_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<ExecutionDetailResponse>, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;
    let snapshot = db::sessions::list_by_execution_with_pending(&state.db_pool, &id).await?;

    let derived = ExecutionResponse::derive_from_snapshot(&exec, &snapshot);
    let mut execution_response: ExecutionResponse = exec.into();
    execution_response.status = derived.status;
    execution_response.completion_eligible = derived.completion_eligible;

    let session_responses: Vec<SessionResponse> = snapshot
        .into_iter()
        .map(|(session, pending)| {
            let status = types::derive_session_display_status(&session, pending);
            let mut resp: SessionResponse = session.into();
            resp.status = status;
            resp
        })
        .collect();

    Ok(Json(ExecutionDetailResponse {
        execution: execution_response,
        sessions: session_responses,
    }))
}

/// Create a new execution (POST /api/executions)
async fn create_execution_handler(
    State(state): State<AppState>,
    Json(req): Json<CreateExecutionRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
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

    let mut seen = std::collections::HashSet::new();
    let all_agent_ids: Vec<String> = req
        .agent_ids
        .iter()
        .filter(|id| seen.insert((*id).clone()))
        .cloned()
        .collect();
    let lead_agent_id = req.root_agent_id.clone();

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

    let _ = state
        .event_broadcast
        .send(EventNotification::persisted(result.execution.id.clone(), 0));

    let derived = derive_execution_fields_async(&state.db_pool, &result.execution).await?;
    let mut exec_resp: ExecutionResponse = result.execution.into();
    exec_resp.status = derived.status;
    exec_resp.completion_eligible = derived.completion_eligible;

    Ok((
        StatusCode::CREATED,
        Json(CreateExecutionResponse {
            execution: exec_resp,
            session_id: result.session_id,
            warning: result.warning,
        }),
    ))
}

/// Sets desired=terminate on the root session.
async fn terminate_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;

    if exec.outcome.is_some() {
        let exec_resp: ExecutionResponse = exec.into();
        return Ok(Json(serde_json::json!({"execution": exec_resp})));
    }

    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;
    let root_session = sessions
        .iter()
        .find(|s| s.parent_session_id.is_none())
        .ok_or_else(|| {
            SchedulerError::NotFound(format!("no root session found for execution {id}"))
        })?;

    let already_terminated = match transition::transition(
        &state.db_pool,
        &id,
        &root_session.id,
        transition::Action::SetDesired(transition::Desired::Terminate, "user".to_string()),
    )
    .await
    {
        Ok(_) => false,
        Err(transition::Rejected::Ratchet) => true,
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "transition failed: {e:?}"
            )));
        }
    };

    if already_terminated && exec.outcome.is_none() {
        let mut fix_tx = db::executions::begin_execution_tx(&state.db_pool, &id)
            .await
            .map_err(|e| SchedulerError::Database(format!("begin fix tx: {e}")))?;
        let tx_root = db::sessions::get_in_tx(&state.db_pool, &mut fix_tx, &root_session.id)
            .await
            .map_err(|e| SchedulerError::Database(format!("recheck root: {e}")))?;
        if tx_root.outcome.is_none() || tx_root.desired != "terminate" {
            let _ = fix_tx.rollback().await;
        } else {
            let derived =
                transition::derive_execution_outcome(&state.db_pool, &mut fix_tx, &id).await?;
            let fix_sql = state.db_pool.prepare_query(
                "UPDATE executions SET desired = 'terminate', outcome = ?, \
                 updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND outcome IS NULL",
            );
            let _ = sqlx::query(&fix_sql)
                .bind(&derived)
                .bind(&id)
                .execute(&mut *fix_tx)
                .await;
            reconciler::emit_execution_repair_event(&state.db_pool, &mut fix_tx, &id, &derived)
                .await;
            let _ = fix_tx.commit().await;
            let _ = state
                .event_broadcast
                .send(crate::app::EventNotification::persisted(id.clone(), 0));
        }
    }

    let fresh_root = db::sessions::get_by_id(&state.db_pool, &root_session.id).await?;
    let _ = reconciler::cascade_children(&state.db_pool, &fresh_root).await;

    state.task_queue.wake_waiters();

    let _ = state
        .event_broadcast
        .send(crate::app::EventNotification::persisted(id.clone(), 0));

    let fresh_exec = db::executions::get_by_id(&state.db_pool, &id).await?;
    let exec_resp: ExecutionResponse = fresh_exec.into();
    Ok(Json(serde_json::json!({"execution": exec_resp})))
}

/// Get events for an execution (GET /api/executions/:id/events)
async fn execution_events(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<EventResponse>>, SchedulerError> {
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

    let agent = db::agents::get_by_id(&state.db_pool, &req.agent_id).await?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent is disabled: {}",
            req.agent_id
        )));
    }

    db::execution_agents::insert(&state.db_pool, &id, &req.agent_id).await?;

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
    desired: String,
    executor_state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    outcome: Option<String>,
    status: String,
    parent_name: Option<String>,
}

/// Derive display status from a discovery entry + pending_turns.
fn derive_discovery_status(
    entry: &db::sessions::SessionDiscoveryEntry,
    pending_turns: i64,
) -> String {
    if let Some(ref outcome) = entry.outcome {
        return outcome.clone();
    }
    if entry.executor_state == "running" || pending_turns > 0 || entry.command_token.is_some() {
        return "working".to_string();
    }
    if entry.desired == "stop" {
        return "stopped".to_string();
    }
    match entry.executor_state.as_str() {
        "idle" => "idle".to_string(),
        "unassigned" => "unassigned".to_string(),
        "crashed" => "crashed".to_string(),
        _ => "unassigned".to_string(),
    }
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

        let status = derive_discovery_status(entry, entry.pending_turns);

        entries.push(SessionDiscoveryResponse {
            session_id: entry.session_id.clone(),
            hierarchical_name: hier_name,
            agent_name: entry.agent_name.clone(),
            role: role.to_string(),
            desired: entry.desired.clone(),
            executor_state: entry.executor_state.clone(),
            outcome: entry.outcome.clone(),
            status,
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
            "/api/executions/{id}/terminate",
            axum::routing::post(terminate_execution),
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
