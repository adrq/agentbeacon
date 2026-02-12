use std::time::Duration;

use axum::{Json, extract::State, http::StatusCode};
use serde::{Deserialize, Serialize};
use tokio::time::Instant;

use serde_json::json;

use crate::app::AppState;
use crate::db;
use crate::queue::TaskAssignment;

const LONG_POLL_TIMEOUT_SECS: u64 = 30;

/// Worker sync request — session-based protocol
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerSyncRequest {
    #[serde(default)]
    pub session_state: Option<SessionState>,
    #[serde(default)]
    pub session_result: Option<SessionResult>,
}

/// Heartbeat / event-poll from a worker that owns a session
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionState {
    pub session_id: String,
    pub status: String, // "running" | "waiting_for_event"
    #[serde(default)]
    pub agent_session_id: Option<String>,
}

/// Worker reporting a just-finished agent turn with the agent's native session ID
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionResult {
    pub session_id: String,
    #[serde(default)]
    pub agent_session_id: Option<String>,
    #[serde(default)]
    pub output: Option<serde_json::Value>,
    #[serde(default)]
    pub error: Option<String>,
}

/// Worker sync response — tagged union
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerSyncResponse {
    NoAction,
    SessionAssigned {
        #[serde(rename = "sessionId")]
        session_id: String,
        task: TaskAssignment,
    },
    PromptDelivery {
        #[serde(rename = "sessionId")]
        session_id: String,
        task: TaskAssignment,
    },
    SessionComplete {
        #[serde(rename = "sessionId")]
        session_id: String,
    },
    Command {
        command: String,
    },
}

/// Handle worker sync endpoint — session-based protocol
pub async fn handle_worker_sync(
    State(state): State<AppState>,
    Json(request): Json<WorkerSyncRequest>,
) -> Result<Json<WorkerSyncResponse>, StatusCode> {
    // Step 1: Process session_result if present
    if let Some(ref result) = request.session_result {
        if let Some(ref agent_session_id) = result.agent_session_id {
            match db::sessions::update_agent_session_id(
                &state.db_pool,
                &result.session_id,
                agent_session_id,
            )
            .await
            {
                Ok(()) => {}
                Err(crate::error::SchedulerError::NotFound(msg)) => {
                    tracing::warn!(
                        session_id = %result.session_id,
                        "session_result for unknown session: {msg}"
                    );
                }
                Err(e) => {
                    tracing::error!("update_agent_session_id failed: {e}");
                    return Err(StatusCode::INTERNAL_SERVER_ERROR);
                }
            }
        }

        // Emit agent message event if output is present
        if let Some(ref output) = result.output {
            match db::sessions::get_by_id(&state.db_pool, &result.session_id).await {
                Ok(session) => {
                    let payload_str = serde_json::to_string(output).unwrap();
                    if let Err(e) = db::events::insert(
                        &state.db_pool,
                        &session.execution_id,
                        Some(&result.session_id),
                        "message",
                        &payload_str,
                    )
                    .await
                    {
                        tracing::error!(
                            session_id = %result.session_id,
                            execution_id = %session.execution_id,
                            error = %e,
                            "failed to insert agent message event"
                        );
                    }
                }
                Err(e) => {
                    tracing::warn!("failed to emit agent message event: {e}");
                }
            }
        }

        // Handle error: transition session to failed and emit error event
        if let Some(ref error_msg) = result.error {
            tracing::warn!(
                session_id = %result.session_id,
                error = %error_msg,
                "Worker reported session error"
            );

            if let Ok(session) = db::sessions::get_by_id(&state.db_pool, &result.session_id).await {
                // Emit error state_change event
                if let Err(e) = db::events::insert(
                    &state.db_pool,
                    &session.execution_id,
                    Some(&result.session_id),
                    "state_change",
                    &serde_json::to_string(&json!({
                        "from": session.status,
                        "to": "failed",
                        "error": error_msg,
                    }))
                    .unwrap(),
                )
                .await
                {
                    tracing::error!(
                        session_id = %result.session_id,
                        error = %e,
                        "failed to insert error state_change event"
                    );
                }

                // Transition session to failed
                if let Err(e) =
                    db::sessions::update_status(&state.db_pool, &result.session_id, "failed").await
                {
                    tracing::error!(
                        session_id = %result.session_id,
                        error = %e,
                        "failed to transition session to failed"
                    );
                }

                // Only propagate to execution for master sessions
                if session.parent_session_id.is_none()
                    && let Err(e) = db::executions::update_status(
                        &state.db_pool,
                        &session.execution_id,
                        "failed",
                    )
                    .await
                {
                    tracing::error!(
                        execution_id = %session.execution_id,
                        error = %e,
                        "failed to transition execution to failed"
                    );
                }
            }
        }
    }

    // Step 2: Handle session_state if present
    if let Some(ref session_state) = request.session_state {
        return match session_state.status.as_str() {
            "waiting_for_event" => long_poll_session(&state, &session_state.session_id).await,
            // "running" or any other status — heartbeat ack
            _ => Ok(Json(WorkerSyncResponse::NoAction)),
        };
    }

    // Step 3: Handle idle worker (no session_state, session_result already processed)
    handle_idle_worker(&state).await
}

/// Long-poll loop: wait for a task in the session inbox or terminal status
async fn long_poll_session(
    state: &AppState,
    session_id: &str,
) -> Result<Json<WorkerSyncResponse>, StatusCode> {
    let deadline = Instant::now() + Duration::from_secs(LONG_POLL_TIMEOUT_SECS);

    loop {
        let notified = state.task_queue.notified();

        // Re-check terminal status every iteration
        let session = match db::sessions::get_by_id(&state.db_pool, session_id).await {
            Ok(s) => s,
            Err(crate::error::SchedulerError::NotFound(_)) => {
                tracing::warn!(
                    session_id,
                    "long-poll for unknown session, releasing worker"
                );
                return Ok(Json(WorkerSyncResponse::SessionComplete {
                    session_id: session_id.to_string(),
                }));
            }
            Err(e) => {
                tracing::error!("long-poll get_by_id failed: {e}");
                return Err(StatusCode::INTERNAL_SERVER_ERROR);
            }
        };

        if matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
            return Ok(Json(WorkerSyncResponse::SessionComplete {
                session_id: session_id.to_string(),
            }));
        }

        if let Some(task) = state
            .task_queue
            .pop_by_session(session_id)
            .await
            .map_err(|e| {
                tracing::error!("long-poll pop_by_session failed: {e}");
                StatusCode::INTERNAL_SERVER_ERROR
            })?
        {
            return Ok(Json(WorkerSyncResponse::PromptDelivery {
                session_id: session_id.to_string(),
                task,
            }));
        }

        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(Json(WorkerSyncResponse::NoAction));
        }

        tokio::select! {
            _ = notified => continue,
            _ = tokio::time::sleep(remaining) => {}
        }
    }
}

/// Idle worker: find and claim an assignable session
async fn handle_idle_worker(state: &AppState) -> Result<Json<WorkerSyncResponse>, StatusCode> {
    // Try up to 2 times (handles race where another worker claims first)
    for _ in 0..2 {
        let session = db::sessions::find_assignable(&state.db_pool)
            .await
            .map_err(|e| {
                tracing::error!("find_assignable failed: {e}");
                StatusCode::INTERNAL_SERVER_ERROR
            })?;

        let session = match session {
            Some(s) => s,
            None => return Ok(Json(WorkerSyncResponse::NoAction)),
        };

        let claimed = db::sessions::claim_assignable(&state.db_pool, &session.id)
            .await
            .map_err(|e| {
                tracing::error!("claim_assignable failed: {e}");
                StatusCode::INTERNAL_SERVER_ERROR
            })?;

        if !claimed {
            // Someone else claimed it — retry
            continue;
        }

        // Emit session state_change event
        let session_state_event = json!({"from": "submitted", "to": "working"});
        db::events::insert(
            &state.db_pool,
            &session.execution_id,
            Some(&session.id),
            "state_change",
            &serde_json::to_string(&session_state_event).unwrap(),
        )
        .await
        .map_err(|e| {
            tracing::error!("session state_change event failed: {e}");
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

        // Transition execution submitted → working (only on first session claim)
        let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id)
            .await
            .map_err(|e| {
                tracing::error!("get execution after claim failed: {e}");
                StatusCode::INTERNAL_SERVER_ERROR
            })?;

        if execution.status == "submitted" {
            db::executions::update_status(&state.db_pool, &session.execution_id, "working")
                .await
                .map_err(|e| {
                    tracing::error!("execution submitted→working failed: {e}");
                    StatusCode::INTERNAL_SERVER_ERROR
                })?;

            let exec_state_event = json!({"from": "submitted", "to": "working"});
            db::events::insert(
                &state.db_pool,
                &session.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_state_event).unwrap(),
            )
            .await
            .map_err(|e| {
                tracing::error!("execution state_change event failed: {e}");
                StatusCode::INTERNAL_SERVER_ERROR
            })?;
        }

        // Claimed successfully — try to pop initial task
        if let Some(task) = state
            .task_queue
            .pop_by_session(&session.id)
            .await
            .map_err(|e| {
                tracing::error!("pop_by_session after claim failed: {e}");
                StatusCode::INTERNAL_SERVER_ERROR
            })?
        {
            return Ok(Json(WorkerSyncResponse::SessionAssigned {
                session_id: session.id,
                task,
            }));
        }

        // Task was consumed by next_instruction — long-poll for the next one
        return long_poll_session(state, &session.id).await;
    }

    Ok(Json(WorkerSyncResponse::NoAction))
}
