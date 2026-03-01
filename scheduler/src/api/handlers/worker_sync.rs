use std::time::Duration;

use axum::{Json, extract::State, http::StatusCode};
use serde::{Deserialize, Serialize};
use tokio::time::Instant;

use serde_json::json;

use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;
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

/// A single turn message with dedup sequence number
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnMessagePayload {
    pub msg_seq: i64,
    pub payload: serde_json::Value,
}

/// Worker reporting a just-finished agent turn with the agent's native session ID
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionResult {
    pub session_id: String,
    #[serde(default)]
    pub agent_session_id: Option<String>,
    #[serde(default)]
    pub turn_messages: Vec<TurnMessagePayload>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub error_kind: Option<String>,
    #[serde(default)]
    pub stderr: Option<String>,
    #[serde(default)]
    pub has_pending_turn: bool,
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

        // Insert turn messages with dedup (mid-turn POSTs may have already inserted some)
        if !result.turn_messages.is_empty() {
            match db::sessions::get_by_id(&state.db_pool, &result.session_id).await {
                Ok(session) => {
                    for msg in &result.turn_messages {
                        let payload_str = serde_json::to_string(&msg.payload).unwrap();
                        match db::events::insert_with_dedup(
                            &state.db_pool,
                            &session.execution_id,
                            &result.session_id,
                            "message",
                            &payload_str,
                            msg.msg_seq,
                        )
                        .await
                        {
                            Ok(Some(event_id)) => {
                                let _ = state.event_broadcast.send(EventNotification {
                                    execution_id: session.execution_id.clone(),
                                    event_id,
                                });
                            }
                            Ok(None) => {
                                // Already delivered via mid-turn POST — skip broadcast
                            }
                            Err(e) => {
                                tracing::error!(
                                    session_id = %result.session_id,
                                    execution_id = %session.execution_id,
                                    msg_seq = msg.msg_seq,
                                    error = %e,
                                    "failed to insert agent message event"
                                );
                            }
                        }
                    }
                }
                Err(e) => {
                    tracing::warn!("failed to emit agent message events: {e}");
                }
            }
        }

        // Handle error_kind (checked BEFORE error field for correct cancelled handling)
        if let Some(ref ek) = result.error_kind {
            match ek.as_str() {
                "cancelled" => {
                    // Cancellation confirmed — cascade was already performed by the
                    // cancel initiator (session cancel or execution cancel endpoint).
                    tracing::info!(
                        session_id = %result.session_id,
                        "Worker reported session cancelled"
                    );
                    if let Ok(session) =
                        db::sessions::get_by_id(&state.db_pool, &result.session_id).await
                        && !matches!(session.status.as_str(), "completed" | "failed" | "canceled")
                    {
                        if let Err(e) = db::sessions::update_status(
                            &state.db_pool,
                            &result.session_id,
                            "canceled",
                        )
                        .await
                        {
                            tracing::error!(
                                session_id = %result.session_id,
                                error = %e,
                                "failed to transition session to canceled"
                            );
                        } else {
                            let event_payload = json!({
                                "from": session.status,
                                "to": "canceled",
                            });
                            if let Ok(event_id) = db::events::insert(
                                &state.db_pool,
                                &session.execution_id,
                                Some(&result.session_id),
                                "state_change",
                                &serde_json::to_string(&event_payload).unwrap(),
                            )
                            .await
                            {
                                let _ = state.event_broadcast.send(EventNotification {
                                    execution_id: session.execution_id.clone(),
                                    event_id,
                                });
                            }
                        }
                    }
                }
                _ => {
                    tracing::warn!(
                        session_id = %result.session_id,
                        error_kind = %ek,
                        error = ?result.error,
                        "Worker reported session failure"
                    );
                    crate::services::crash::handle_session_failure(
                        &state.db_pool,
                        &state.task_queue,
                        &state.event_broadcast,
                        &result.session_id,
                        Some(ek.as_str()),
                        result.error.as_deref(),
                        result.stderr.as_deref(),
                    )
                    .await;
                }
            }
        } else if result.error.is_some() {
            // No error_kind but error present — fail closed rather than leaving
            // the session stuck in working.
            tracing::warn!(
                session_id = %result.session_id,
                error = ?result.error,
                "Worker reported error without error_kind — treating as failure"
            );
            crate::services::crash::handle_session_failure(
                &state.db_pool,
                &state.task_queue,
                &state.event_broadcast,
                &result.session_id,
                None,
                result.error.as_deref(),
                result.stderr.as_deref(),
            )
            .await;
        }
    }

    // Step 1b: After processing a successful result, check for queued tasks
    // before falling through to session_state handling. This allows queued
    // mid-turn messages to be returned in the same sync response.
    if let Some(ref result) = request.session_result {
        let has_error = result.error_kind.is_some() || result.error.is_some();

        if !has_error {
            // Check if a user message was queued during the turn (always check,
            // even when worker has pending turns — a concurrent user message may
            // have arrived)
            if let Some(task) = state
                .task_queue
                .pop_by_session(&result.session_id)
                .await
                .map_err(|e| {
                    tracing::error!("post-result pop_by_session failed: {e}");
                    StatusCode::INTERNAL_SERVER_ERROR
                })?
            {
                return Ok(Json(WorkerSyncResponse::PromptDelivery {
                    session_id: result.session_id.clone(),
                    task,
                }));
            }

            // Only transition to input-required if the worker has no pending
            // turns locally. When has_pending_turn is true, the worker already
            // has the next prompt queued and will start processing it
            // immediately — transitioning to input-required would be incorrect.
            if !result.has_pending_turn
                && let Ok(session) =
                    db::sessions::get_by_id(&state.db_pool, &result.session_id).await
                && session.status == "working"
            {
                if let Err(e) = db::sessions::update_status(
                    &state.db_pool,
                    &result.session_id,
                    "input-required",
                )
                .await
                {
                    tracing::error!(
                        session_id = %result.session_id,
                        error = %e,
                        "failed to transition session to input-required"
                    );
                } else {
                    let event_payload = json!({
                        "from": "working",
                        "to": "input-required",
                    });
                    match db::events::insert(
                        &state.db_pool,
                        &session.execution_id,
                        Some(&result.session_id),
                        "state_change",
                        &serde_json::to_string(&event_payload).unwrap(),
                    )
                    .await
                    {
                        Ok(event_id) => {
                            let _ = state.event_broadcast.send(EventNotification {
                                execution_id: session.execution_id.clone(),
                                event_id,
                            });
                        }
                        Err(e) => {
                            tracing::warn!(
                                session_id = %result.session_id,
                                error = %e,
                                "failed to insert input-required state_change event"
                            );
                        }
                    }

                    // Turn-complete auto-notification — deliver to parent
                    if session.parent_session_id.is_some()
                        && let Some(output_text) =
                            crate::services::notification::extract_turn_output(
                                &result.turn_messages,
                            )
                        && let Err(e) = crate::services::notification::deliver_to_parent(
                            &state.db_pool,
                            &state.task_queue,
                            &state.event_broadcast,
                            &result.session_id,
                            &output_text,
                        )
                        .await
                    {
                        tracing::error!(
                            session_id = %result.session_id,
                            error = %e,
                            "failed to deliver turn-complete to parent"
                        );
                    }

                    // Child sessions: worker stays attached and enters long-poll,
                    // waiting for the parent (or lateral message) to send more work.
                    // The subprocess stays alive, preserving in-process state.
                    if session.parent_session_id.is_some() {
                        return Ok(Json(WorkerSyncResponse::NoAction));
                    }

                    // Propagate to execution for lead sessions
                    if session.parent_session_id.is_none() {
                        if let Err(e) = db::executions::update_status(
                            &state.db_pool,
                            &session.execution_id,
                            "input-required",
                        )
                        .await
                        {
                            tracing::warn!(
                                execution_id = %session.execution_id,
                                error = %e,
                                "failed to transition execution to input-required"
                            );
                        }

                        let exec_event = json!({
                            "from": "working",
                            "to": "input-required",
                        });
                        match db::events::insert(
                            &state.db_pool,
                            &session.execution_id,
                            None,
                            "state_change",
                            &serde_json::to_string(&exec_event).unwrap(),
                        )
                        .await
                        {
                            Ok(event_id) => {
                                let _ = state.event_broadcast.send(EventNotification {
                                    execution_id: session.execution_id.clone(),
                                    event_id,
                                });
                            }
                            Err(e) => {
                                tracing::warn!(
                                    execution_id = %session.execution_id,
                                    error = %e,
                                    "failed to insert execution input-required state_change event"
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    // Step 2: Handle session_state if present
    if let Some(ref session_state) = request.session_state {
        // Heartbeat: touch updated_at so recovery scan knows this worker is alive
        if let Err(e) =
            db::sessions::touch_updated_at(&state.db_pool, &session_state.session_id).await
        {
            tracing::warn!(error = %e, session_id = %session_state.session_id, "heartbeat touch failed");
        }

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
        let event_id = db::events::insert(
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
        let _ = state.event_broadcast.send(EventNotification {
            execution_id: session.execution_id.clone(),
            event_id,
        });

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
            let event_id = db::events::insert(
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
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id,
            });
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

        // Task was consumed between enqueue and claim — long-poll for the next one
        return long_poll_session(state, &session.id).await;
    }

    Ok(Json(WorkerSyncResponse::NoAction))
}

// --- Mid-turn message event endpoint ---

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerMessageRequest {
    pub session_id: String,
    pub execution_id: String,
    pub msg_seq: i64,
    pub payload: serde_json::Value,
}

/// Handle mid-turn message events POSTed by the worker during an active turn.
pub async fn handle_worker_event(
    State(state): State<AppState>,
    Json(request): Json<WorkerMessageRequest>,
) -> Result<StatusCode, SchedulerError> {
    // Resolve execution_id server-side to prevent misattribution.
    // The dedup index is (session_id, msg_seq) — a wrong execution_id would
    // win the race and block the correct sync-path insert.
    let session = db::sessions::get_by_id(&state.db_pool, &request.session_id).await?;

    if session.execution_id != request.execution_id {
        tracing::warn!(
            session_id = %request.session_id,
            expected_execution_id = %session.execution_id,
            received_execution_id = %request.execution_id,
            "worker event execution_id mismatch, using server value"
        );
    }

    let payload_str = serde_json::to_string(&request.payload)
        .map_err(|e| SchedulerError::ValidationFailed(format!("invalid payload: {e}")))?;

    if let Some(event_id) = db::events::insert_with_dedup(
        &state.db_pool,
        &session.execution_id,
        &request.session_id,
        "message",
        &payload_str,
        request.msg_seq,
    )
    .await?
    {
        let _ = state.event_broadcast.send(EventNotification {
            execution_id: session.execution_id,
            event_id,
        });
    }

    Ok(StatusCode::CREATED)
}
