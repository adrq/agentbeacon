//! Worker sync handler.

use axum::{Json, extract::State, http::StatusCode};
use serde::{Deserialize, Serialize};

use crate::app::AppState;

fn is_retryable_sqlx(e: &sqlx::Error) -> bool {
    if let sqlx::Error::Database(db_err) = e
        && let Some(code) = db_err.code()
    {
        return code == "40P01" || code == "40001";
    }
    false
}
use crate::db;
use crate::services::reconciler::{self, CommandAction, ReconcilerAction};
use crate::services::transition::{self, ExState};

#[derive(Debug, Clone, Deserialize)]
pub struct WorkerSyncRequest {
    pub worker_id: String,
    #[serde(default)]
    pub executor_report: Option<ExecutorReport>,
    #[serde(default)]
    pub turn_result: Option<TurnResult>,
    #[serde(default)]
    pub command_ack: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExecutorReport {
    pub session_id: String,
    pub executor_state: String,
    #[serde(default)]
    pub agent_session_id: Option<String>,
}

/// A single turn message with optional dedup sequence number
#[derive(Debug, Clone, Deserialize)]
pub struct TurnMessagePayload {
    #[serde(default)]
    pub msg_seq: Option<i64>,
    #[serde(flatten)]
    pub payload: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TurnResult {
    pub session_id: String,
    #[serde(default)]
    pub messages: Vec<TurnMessagePayload>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub error_kind: Option<String>,
    #[serde(default)]
    pub stderr: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(clippy::large_enum_variant)]
pub enum WorkerSyncResponse {
    NoAction,
    Command {
        token: String,
        action: CommandAction,
    },
}

pub async fn worker_sync(
    State(state): State<AppState>,
    Json(req): Json<WorkerSyncRequest>,
) -> Result<Json<WorkerSyncResponse>, (StatusCode, String)> {
    let pool = &state.db_pool;
    let worker_id = &req.worker_id;

    {
        let mut heartbeats = state.worker_heartbeats.write().unwrap();
        heartbeats.insert(worker_id.clone(), std::time::Instant::now());
    }

    let mut skip_reconciler = false;
    let mut executor_report_succeeded = false;
    let mut session_scoped_reconcile: Option<String> = None;
    if let Some(report) = &req.executor_report {
        let crash_meta = if report.executor_state == "crashed" {
            req.turn_result
                .as_ref()
                .filter(|tr| tr.session_id == report.session_id)
                .map(|tr| transition::CrashMeta {
                    error: tr.error.clone(),
                    error_kind: tr.error_kind.clone(),
                    stderr: tr.stderr.clone(),
                })
        } else {
            None
        };
        match process_executor_report(pool, worker_id, report, crash_meta, &state).await {
            Ok(applied) => {
                executor_report_succeeded = applied;
                if !applied || report.executor_state == "crashed" {
                    skip_reconciler = true;
                }
            }
            Err(e) => {
                tracing::warn!("executor_report error for {}: {e}", report.session_id);

                if report.executor_state == "crashed" {
                    skip_reconciler = true;
                } else {
                    if let Ok(session) = db::sessions::get_by_id(pool, &report.session_id).await {
                        if (session.outcome.is_some() && session.desired != "terminate")
                            || session.executor_state != report.executor_state
                            || session.worker_id.as_deref() != Some(worker_id)
                        {
                            if session.worker_id.as_deref() != Some(worker_id) {
                                state.supervisor.kill_worker(worker_id).await;
                            }
                            skip_reconciler = true;
                        } else {
                            session_scoped_reconcile = Some(report.session_id.clone());
                        }
                    } else {
                        skip_reconciler = true;
                    }
                }
            }
        }
    }

    let needs_result_tx = req.turn_result.is_some() || req.command_ack.is_some();
    let mut turn_result_session: Option<db::sessions::Session> = None;
    let mut was_terminal_cancel = false;
    let should_notify = req
        .executor_report
        .as_ref()
        .is_some_and(|r| r.executor_state == "idle")
        && req.executor_report.as_ref().map(|r| &r.session_id)
            == req.turn_result.as_ref().map(|r| &r.session_id)
        && req
            .turn_result
            .as_ref()
            .is_some_and(|r| !r.messages.is_empty());

    let mut notify_parent: Option<db::sessions::Session> = None;

    if needs_result_tx {
        if let Some(result) = &req.turn_result
            && let Ok(session) = db::sessions::get_by_id(pool, &result.session_id).await
        {
            if session.worker_id.as_deref() == Some(worker_id) {
                turn_result_session = Some(session);
            } else {
                tracing::debug!("stale turn_result dropped");
                state.supervisor.kill_worker(worker_id).await;
                skip_reconciler = true;
            }
        }

        if should_notify
            && let Some(ref session) = turn_result_session
            && session.outcome.is_none()
            && session.desired == "run"
            && let Some(ref parent_id) = session.parent_session_id
            && let Ok(parent) = db::sessions::get_by_id(pool, parent_id).await
            && parent.outcome.is_none()
            && let Ok(exec) = db::executions::get_by_id(pool, &session.execution_id).await
            && exec.desired != "terminate"
            && exec.outcome.is_none()
        {
            notify_parent = Some(parent);
        }

        let mut result_tx_execution_id =
            turn_result_session.as_ref().map(|s| s.execution_id.clone());

        if result_tx_execution_id.is_none()
            && let Some(ref ack_token) = req.command_ack
        {
            let ack_sql =
                pool.prepare_query("SELECT execution_id FROM sessions WHERE command_token = ?");
            if let Ok(Some(row)) = sqlx::query(&ack_sql)
                .bind(ack_token.as_str())
                .fetch_optional(pool.as_ref())
                .await
            {
                result_tx_execution_id = Some(row.get::<String, _>("execution_id"));
            }
        }

        let mut turn_result_stale_worker = false;
        let mut ack_stale_worker = false;

        for attempt in 0..=3u32 {
            if attempt > 0 {
                tokio::time::sleep(std::time::Duration::from_millis(
                    50 * (1u64 << (attempt - 1)),
                ))
                .await;
            }
            let trs_in = turn_result_session.clone();
            let result: Result<_, Box<dyn std::error::Error + Send + Sync>> = async {
                let mut turn_result_session_local = trs_in;
                let mut was_terminal_cancel_local = false;
                let mut ack_stale_worker_local = false;
                let mut turn_result_stale_worker_local = false;
                let mut skip_reconciler_local = false;

                let mut tx = if let Some(ref exec_id) = result_tx_execution_id {
                    db::executions::begin_execution_tx(pool, exec_id)
                        .await
                        .inspect_err(|e| {
                            tracing::warn!(attempt, error = %e, "begin execution tx failed")
                        })
                        .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> {
                            e.to_string().into()
                        })?
                } else {
                    pool.begin()
                        .await
                        .inspect_err(|e| tracing::warn!(attempt, error = %e, "begin tx failed"))
                        .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> { Box::new(e) })?
                };

                let mut tx_child_session: Option<db::sessions::Session> = None;
                if let Some(result) = &req.turn_result
                    && turn_result_session_local.is_some()
                {
                    match db::sessions::get_in_tx(pool, &mut tx, &result.session_id).await {
                        Ok(s) if s.worker_id.as_deref() == Some(worker_id) => {
                            process_turn_result_in_tx(pool, &mut tx, &s, result)
                                .await
                                .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> {
                                    e.into()
                                })?;
                            tx_child_session = Some(s);
                        }
                        Ok(_) => {
                            tracing::debug!(
                                "turn_result dropped: worker_id changed inside tx for session {}",
                                result.session_id
                            );
                            turn_result_session_local = None;
                            turn_result_stale_worker_local = true;
                            skip_reconciler_local = true;
                        }
                        Err(_) => {
                            turn_result_session_local = None;
                        }
                    }
                }

                if should_notify && turn_result_session_local.is_some() {
                    let child_ok = tx_child_session
                        .as_ref()
                        .is_some_and(|s| s.desired == "run" && s.outcome.is_none());

                    let mut tx_parent_session: Option<db::sessions::Session> = None;
                    let notify_still_valid = if !child_ok {
                        false
                    } else if notify_parent.is_some() {
                        let tx_exec = db::executions::get_in_tx(
                            pool,
                            &mut tx,
                            &tx_child_session.as_ref().unwrap().execution_id,
                        )
                        .await;
                        let exec_ok = tx_exec
                            .as_ref()
                            .is_ok_and(|e| e.desired != "terminate" && e.outcome.is_none());

                        let parent_ok = if exec_ok {
                            let tx_parent = db::sessions::get_in_tx(
                                pool,
                                &mut tx,
                                &notify_parent.as_ref().unwrap().id,
                            )
                            .await;
                            let ok = tx_parent
                                .as_ref()
                                .is_ok_and(|p| p.outcome.is_none() && p.desired != "terminate");
                            if ok {
                                tx_parent_session = tx_parent.ok();
                            }
                            ok
                        } else {
                            false
                        };
                        exec_ok && parent_ok
                    } else {
                        true
                    };

                    if notify_still_valid && let Some(ref session) = turn_result_session_local {
                        let notification_data = serde_json::json!({
                            "type": "turn_complete",
                            "child_session_id": &session.id,
                        });

                        if let Some(ref parent) = notify_parent {
                            let parent_id = &parent.id;

                            let parent_is_stopped = tx_parent_session
                                .as_ref()
                                .is_some_and(|p| p.desired == "stop");
                            if parent_is_stopped {
                                let resume_sql = pool.prepare_query(
                                    "UPDATE sessions SET desired = 'run', desired_by = 'system:turn_complete_notify', \
                                     desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
                                     WHERE id = ? AND desired = 'stop'",
                                );
                                let resume_result = sqlx::query(&resume_sql)
                                    .bind(parent_id)
                                    .execute(&mut *tx)
                                    .await
                                    .inspect_err(|e| {
                                        tracing::warn!(attempt, error = %e, "auto-resume parent failed")
                                    })
                                    .map_err(
                                        |e| -> Box<dyn std::error::Error + Send + Sync> {
                                            Box::new(e)
                                        },
                                    )?;

                                if resume_result.rows_affected() > 0 {
                                    let resume_event = serde_json::json!({"desired": "run", "desired_by": "system:turn_complete_notify"});
                                    let resume_event_str =
                                        serde_json::to_string(&resume_event).unwrap_or_default();
                                    let sc_sql = pool.prepare_query(
                                        "INSERT INTO events (execution_id, session_id, event_type, payload) \
                                         VALUES (?, ?, 'state_change', ?)",
                                    );
                                    sqlx::query(&sc_sql)
                                        .bind(&session.execution_id)
                                        .bind(parent_id)
                                        .bind(&resume_event_str)
                                        .execute(&mut *tx)
                                        .await
                                        .inspect_err(|e| {
                                            tracing::warn!(
                                                attempt,
                                                error = %e,
                                                "auto-resume state_change event failed"
                                            )
                                        })
                                        .map_err(
                                            |e| -> Box<dyn std::error::Error + Send + Sync> {
                                                Box::new(e)
                                            },
                                        )?;
                                }
                            }

                            let notif_text =
                                format!("Child session {} turn complete.", session.id);
                            let notification = serde_json::json!({
                                "message": {
                                    "role": "ROLE_USER",
                                    "parts": [
                                        {"text": notif_text},
                                        {"data": {
                                            "type": "turn_complete",
                                            "child_session_id": &session.id,
                                        }}
                                    ]
                                }
                            });
                            let payload_json =
                                serde_json::to_string(&notification).unwrap_or_default();
                            let source = format!("child_result:{}", session.id);
                            let insert_sql = pool.prepare_query(
                                "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
                            );
                            sqlx::query(&insert_sql)
                                .bind(&session.execution_id)
                                .bind(parent_id)
                                .bind(&payload_json)
                                .bind(&source)
                                .execute(&mut *tx)
                                .await
                                .inspect_err(|e| {
                                    tracing::warn!(
                                        attempt,
                                        error = %e,
                                        "turn-complete notification enqueue failed"
                                    )
                                })
                                .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> {
                                    Box::new(e)
                                })?;

                            let platform_payload = serde_json::json!({
                                "parts": [{"data": notification_data}]
                            });
                            let platform_str =
                                serde_json::to_string(&platform_payload).unwrap_or_default();
                            let event_sql = pool.prepare_query(
                                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                                 VALUES (?, ?, 'platform', ?) RETURNING id",
                            );
                            sqlx::query(&event_sql)
                                .bind(&session.execution_id)
                                .bind(parent_id)
                                .bind(&platform_str)
                                .execute(&mut *tx)
                                .await
                                .inspect_err(|e| {
                                    tracing::warn!(
                                        attempt,
                                        error = %e,
                                        "turn-complete platform event failed"
                                    )
                                })
                                .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> {
                                    Box::new(e)
                                })?;
                        } else if session.parent_session_id.is_none() {
                            let platform_payload = serde_json::json!({
                                "parts": [{"data": notification_data}]
                            });
                            let platform_str =
                                serde_json::to_string(&platform_payload).unwrap_or_default();
                            let event_sql = pool.prepare_query(
                                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                                 VALUES (?, ?, 'platform', ?) RETURNING id",
                            );
                            let _ = sqlx::query(&event_sql)
                                .bind(&session.execution_id)
                                .bind(&session.id)
                                .bind(&platform_str)
                                .execute(&mut *tx)
                                .await;
                        }
                    }
                }

                if let Some(ack_token) = &req.command_ack {
                    match process_command_ack_in_tx(pool, &mut tx, worker_id, ack_token).await {
                        Ok(AckResult::Applied { terminal_cancel }) => {
                            was_terminal_cancel_local = terminal_cancel
                        }
                        Ok(AckResult::StaleWorker) => {
                            ack_stale_worker_local = true;
                            skip_reconciler_local = true;
                        }
                        Ok(AckResult::StaleToken) => {}
                        Err(e) => {
                            return Err(format!("command_ack failed: {e}").into());
                        }
                    }
                }

                tx.commit()
                    .await
                    .inspect_err(|e| tracing::warn!(attempt, error = %e, "commit tx failed"))
                    .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> { Box::new(e) })?;

                Ok((
                    turn_result_session_local,
                    was_terminal_cancel_local,
                    ack_stale_worker_local,
                    turn_result_stale_worker_local,
                    skip_reconciler_local,
                ))
            }
            .await;

            match result {
                Ok((trs, wtc, asw, trsw, ps)) => {
                    turn_result_session = trs;
                    was_terminal_cancel = wtc;
                    ack_stale_worker = asw;
                    turn_result_stale_worker = trsw;
                    skip_reconciler |= ps;
                    break;
                }
                Err(e)
                    if (e
                        .downcast_ref::<sqlx::Error>()
                        .is_some_and(is_retryable_sqlx)
                        || {
                            let msg = e.to_string();
                            msg.contains("deadlock detected") || msg.contains("could not serialize")
                        })
                        && attempt < 3 =>
                {
                    tracing::warn!(attempt, error = %e, "sync deadlock, retrying");
                }
                Err(e) => {
                    return Err((StatusCode::INTERNAL_SERVER_ERROR, format!("{e}")));
                }
            }
        }

        if ack_stale_worker || turn_result_stale_worker {
            state.supervisor.kill_worker(worker_id).await;
        }

        if let Some(ref session) = turn_result_session {
            let _ = state
                .event_broadcast
                .send(crate::app::EventNotification::persisted(
                    session.execution_id.clone(),
                    0,
                ));
        }
    }

    if executor_report_succeeded
        && turn_result_session.is_none()
        && let Some(report) = &req.executor_report
        && let Ok(s) = db::sessions::get_by_id(pool, &report.session_id).await
    {
        let _ = state
            .event_broadcast
            .send(crate::app::EventNotification::persisted(s.execution_id, 0));
    }

    if was_terminal_cancel {
        skip_reconciler = true;
    }

    if needs_result_tx {
        state.task_queue.wake_waiters();
    }

    if skip_reconciler {
        return Ok(Json(WorkerSyncResponse::NoAction));
    }

    if let Some(session_id) = session_scoped_reconcile {
        return match run_reconciler_for_session(pool, worker_id, &session_id, &state).await {
            ReconcilerResult::Command(resp) => Ok(Json(resp)),
            ReconcilerResult::PendingCommand | ReconcilerResult::NoAction => {
                Ok(Json(WorkerSyncResponse::NoAction))
            }
            ReconcilerResult::NothingFound => Ok(Json(WorkerSyncResponse::NoAction)),
        };
    }

    let is_flush = req.command_ack.is_some() || req.turn_result.is_some();

    match run_reconciler_for_worker(pool, worker_id, &state).await {
        ReconcilerResult::Command(resp) => Ok(Json(resp)),
        ReconcilerResult::PendingCommand => Ok(Json(WorkerSyncResponse::NoAction)),
        ReconcilerResult::NoAction | ReconcilerResult::NothingFound if is_flush => {
            Ok(Json(WorkerSyncResponse::NoAction))
        }
        ReconcilerResult::NoAction | ReconcilerResult::NothingFound => {
            let timeout_secs = state.long_poll_timeout_secs;
            if timeout_secs == 0 {
                return Ok(Json(WorkerSyncResponse::NoAction));
            }

            let notified = state.task_queue.notified();

            match run_reconciler_for_worker(pool, worker_id, &state).await {
                ReconcilerResult::Command(resp) => return Ok(Json(resp)),
                ReconcilerResult::PendingCommand => return Ok(Json(WorkerSyncResponse::NoAction)),
                _ => {}
            }

            let _ =
                tokio::time::timeout(std::time::Duration::from_secs(timeout_secs), notified).await;

            {
                let mut heartbeats = state.worker_heartbeats.write().unwrap();
                heartbeats.insert(worker_id.to_string(), std::time::Instant::now());
            }

            if let ReconcilerResult::Command(resp) =
                run_reconciler_for_worker(pool, worker_id, &state).await
            {
                return Ok(Json(resp));
            }

            Ok(Json(WorkerSyncResponse::NoAction))
        }
    }
}

/// Process an executor report: update session state via transition function.
/// Returns Ok(true) if the report was applied, Ok(false) if it was dropped (stale).
async fn process_executor_report(
    pool: &db::DbPool,
    worker_id: &str,
    report: &ExecutorReport,
    crash_meta: Option<transition::CrashMeta>,
    state: &AppState,
) -> Result<bool, String> {
    let session = db::sessions::get_by_id(pool, &report.session_id)
        .await
        .map_err(|e| format!("session not found: {e}"))?;

    if session.worker_id.as_deref() != Some(worker_id) {
        tracing::debug!(
            "stale worker report dropped: session {} owned by {:?}, report from {}",
            session.id,
            session.worker_id,
            worker_id
        );
        state.supervisor.kill_worker(worker_id).await;
        return Ok(false);
    }

    let state = match report.executor_state.as_str() {
        "running" => ExState::Running,
        "idle" => ExState::Idle,
        "crashed" => ExState::Crashed,
        other => return Err(format!("unknown executor_state: {other}")),
    };

    if let Some(ref asid) = report.agent_session_id {
        db::sessions::update_agent_session_id(pool, &session.id, asid, worker_id)
            .await
            .map_err(|e| format!("update agent_session_id failed: {e}"))?;
    }

    transition::transition(
        pool,
        &session.execution_id,
        &session.id,
        transition::Action::SetExecutorState(state, worker_id.to_string(), crash_meta),
    )
    .await
    .map_err(|e| format!("transition failed: {e}"))?;

    Ok(true)
}

/// Process turn result inside caller's transaction.
/// Caller pre-fetches session and validates worker ownership.
/// SSE broadcast is caller's responsibility post-commit.
async fn process_turn_result_in_tx(
    pool: &db::DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    session: &db::sessions::Session,
    result: &TurnResult,
) -> Result<(), String> {
    if !result.messages.is_empty() || result.error.is_some() {
        for msg in &result.messages {
            let payload_str = serde_json::to_string(&msg.payload)
                .map_err(|e| format!("serialize message failed: {e}"))?;

            if let Some(seq) = msg.msg_seq {
                let sql = pool.prepare_query(
                    "INSERT INTO events (execution_id, session_id, event_type, payload, msg_seq) \
                     VALUES (?, ?, 'message', ?, ?) \
                     ON CONFLICT (session_id, msg_seq) DO NOTHING",
                );
                sqlx::query(&sql)
                    .bind(&session.execution_id)
                    .bind(&session.id)
                    .bind(&payload_str)
                    .bind(seq)
                    .execute(&mut **tx)
                    .await
                    .map_err(|e| format!("insert message event failed: {e}"))?;
            } else {
                let sql = pool.prepare_query(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) \
                     VALUES (?, ?, 'message', ?)",
                );
                sqlx::query(&sql)
                    .bind(&session.execution_id)
                    .bind(&session.id)
                    .bind(&payload_str)
                    .execute(&mut **tx)
                    .await
                    .map_err(|e| format!("insert message event failed: {e}"))?;
            }
        }

        if let Some(ref error) = result.error {
            let error_payload = serde_json::json!({
                "error": error,
                "error_kind": result.error_kind,
            });
            let payload_str = serde_json::to_string(&error_payload).unwrap_or_default();
            let sql = pool.prepare_query(
                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                 VALUES (?, ?, 'platform', ?)",
            );
            let _ = sqlx::query(&sql)
                .bind(&session.execution_id)
                .bind(&session.id)
                .bind(&payload_str)
                .execute(&mut **tx)
                .await;
        }
    }

    Ok(())
}

enum AckResult {
    Applied { terminal_cancel: bool },
    StaleWorker,
    StaleToken,
}

/// Process command acknowledgment inside caller's transaction.
async fn process_command_ack_in_tx(
    pool: &db::DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    worker_id: &str,
    ack_token: &str,
) -> Result<AckResult, String> {
    let sql = pool.prepare_query(
        "SELECT id, execution_id, worker_id, command_type, outcome FROM sessions WHERE command_token = ?",
    );
    let row = sqlx::query(&sql)
        .bind(ack_token)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| format!("query command_token failed: {e}"))?;

    let Some(row) = row else {
        tracing::debug!(ack_token, worker_id, "ack: token not found (stale)");
        return Ok(AckResult::StaleToken);
    };

    let session_id: String = row.get("id");
    let session_worker_id: Option<String> = row.get("worker_id");
    let command_type: Option<String> = row.get("command_type");

    if session_worker_id.as_deref() != Some(worker_id) {
        tracing::debug!(
            ack_token,
            worker_id,
            ?session_worker_id,
            "ack: stale worker, dropping"
        );
        return Ok(AckResult::StaleWorker);
    }

    let session_outcome: Option<String> = row.get("outcome");
    let is_terminal_cancel = command_type.as_deref() == Some("cancel") && session_outcome.is_some();
    let clear_sql = if is_terminal_cancel {
        pool.prepare_query(
            "UPDATE sessions SET command_token = NULL, command_type = NULL, \
             command_at = NULL, command_has_payload = FALSE, worker_id = NULL, \
             executor_state = CASE WHEN executor_state NOT IN ('idle', 'crashed') \
               THEN 'crashed' ELSE executor_state END, \
             updated_at = CURRENT_TIMESTAMP WHERE id = ? AND command_token = ?",
        )
    } else {
        pool.prepare_query(
            "UPDATE sessions SET command_token = NULL, command_type = NULL, \
             command_at = NULL, command_has_payload = FALSE, \
             updated_at = CURRENT_TIMESTAMP WHERE id = ? AND command_token = ?",
        )
    };

    let ack_result = sqlx::query(&clear_sql)
        .bind(&session_id)
        .bind(ack_token)
        .execute(&mut **tx)
        .await
        .map_err(|e| format!("clear command fields failed: {e}"))?;

    if ack_result.rows_affected() == 0 {
        tracing::debug!(
            session_id = %session_id,
            ack_token,
            "ack: command_token already cleared (stale)"
        );
        return Ok(AckResult::StaleToken);
    }

    tracing::info!(
        session_id = %session_id,
        ack_token,
        command_type = ?command_type,
        is_terminal_cancel,
        "ack processed"
    );

    Ok(AckResult::Applied {
        terminal_cancel: is_terminal_cancel,
    })
}

use sqlx::Row;

#[allow(clippy::large_enum_variant)]
enum ReconcilerResult {
    /// Reconciler produced a command to send.
    Command(WorkerSyncResponse),
    /// Reconciler ran on assigned sessions, no command needed.
    NoAction,
    /// A command is pending (awaiting ack) — return immediately, don't long-poll.
    PendingCommand,
    /// No sessions found for this worker, no unassigned work — caller should long-poll.
    NothingFound,
}

/// Evaluate sessions and produce commands for this worker.
async fn run_reconciler_for_worker(
    pool: &db::DbPool,
    worker_id: &str,
    state: &AppState,
) -> ReconcilerResult {
    let sessions = match db::sessions::find_sessions_for_worker(pool, worker_id).await {
        Ok(s) => s,
        Err(_) => return ReconcilerResult::NothingFound,
    };

    if !sessions.is_empty() {
        let mut sessions_mutated = false;
        let mut has_pending_command = false;
        for _pass in 0..2 {
            let fresh = match db::sessions::find_sessions_for_worker(pool, worker_id).await {
                Ok(s) => s,
                Err(_) => break,
            };
            let mut any_repaired = false;
            for session in &fresh {
                let pending = db::sessions::count_pending_turns(pool, &session.id)
                    .await
                    .unwrap_or(0);
                let execution = match db::executions::get_by_id(pool, &session.execution_id).await {
                    Ok(e) => e,
                    Err(_) => continue,
                };

                match reconciler::reconcile(
                    pool,
                    session,
                    pending,
                    &execution,
                    true,
                    Some(worker_id),
                    Some(&state.supervisor),
                )
                .await
                {
                    Ok(ReconcilerAction::SendCommand { token, action }) => {
                        return ReconcilerResult::Command(WorkerSyncResponse::Command {
                            token,
                            action,
                        });
                    }
                    Ok(ReconcilerAction::Repaired) => {
                        any_repaired = true;
                        sessions_mutated = true;
                    }
                    Ok(ReconcilerAction::Mutated) => {
                        sessions_mutated = true;
                    }
                    Ok(ReconcilerAction::PendingCommand) => {
                        has_pending_command = true;
                    }
                    Ok(ReconcilerAction::NoAction) => {}
                    Err(e) => {
                        tracing::warn!("reconciler error for session {}: {e}", session.id);
                    }
                }
            }
            if !any_repaired {
                break;
            }
        }
        if sessions_mutated {
            state.task_queue.wake_waiters();
        }
        if has_pending_command {
            return ReconcilerResult::PendingCommand;
        }
        return ReconcilerResult::NoAction;
    }

    if let Ok(Some(session)) = db::sessions::find_unassigned_with_work(pool).await {
        let pending = db::sessions::count_pending_turns(pool, &session.id)
            .await
            .unwrap_or(0);
        if let Ok(execution) = db::executions::get_by_id(pool, &session.execution_id).await
            && let Ok(ReconcilerAction::SendCommand { token, action }) = reconciler::reconcile(
                pool,
                &session,
                pending,
                &execution,
                true,
                Some(worker_id),
                Some(&state.supervisor),
            )
            .await
        {
            return ReconcilerResult::Command(WorkerSyncResponse::Command { token, action });
        }
    }

    let mut global_mutated = false;
    if let Ok(all_sessions) = db::sessions::find_reconcilable(pool).await {
        let heartbeats = state.worker_heartbeats.read().unwrap().clone();
        let hb_timeout = std::time::Duration::from_secs(state.heartbeat_timeout_secs);

        for session in &all_sessions {
            if session.worker_id.as_deref() == Some(worker_id) {
                continue;
            }

            if let Some(ref sess_worker_id) = session.worker_id {
                let expired = match heartbeats.get(sess_worker_id.as_str()) {
                    Some(last_seen) => last_seen.elapsed() > hb_timeout,
                    None => state.scheduler_started_at.elapsed() > hb_timeout,
                };
                if expired {
                    if session.command_has_payload {
                        let event_payload = serde_json::json!({
                            "message": "Agent recovered from a crash. A message may have been lost."
                        });
                        let _ = db::events::insert(
                            pool,
                            &session.execution_id,
                            Some(&session.id),
                            "platform",
                            &serde_json::to_string(&event_payload).unwrap_or_default(),
                        )
                        .await;
                    }
                    state.supervisor.kill_worker(sess_worker_id).await;
                    let _ = transition::transition(
                        pool,
                        &session.execution_id,
                        &session.id,
                        transition::Action::DetectCrash,
                    )
                    .await;
                    global_mutated = true;
                    continue;
                }

                if session.outcome.is_none() {
                    let needs_global_reconciliation = session.command_token.is_some()
                        || session.desired == "terminate"
                        || session.executor_state == "crashed";
                    if !needs_global_reconciliation {
                        continue;
                    }
                }
            }

            if session.worker_id.is_none()
                && session.outcome.is_none()
                && session.desired != "terminate"
                && session.executor_state != "crashed"
            {
                continue;
            }

            let pending = db::sessions::count_pending_turns(pool, &session.id)
                .await
                .unwrap_or(0);
            if let Ok(execution) = db::executions::get_by_id(pool, &session.execution_id).await {
                match reconciler::reconcile(
                    pool,
                    session,
                    pending,
                    &execution,
                    false,
                    None,
                    Some(&state.supervisor),
                )
                .await
                {
                    Ok(ReconcilerAction::Mutated) => {
                        global_mutated = true;
                        let _ =
                            state
                                .event_broadcast
                                .send(crate::app::EventNotification::persisted(
                                    session.execution_id.clone(),
                                    0,
                                ));
                    }
                    Ok(ReconcilerAction::Repaired) => {
                        global_mutated = true;
                        let _ =
                            state
                                .event_broadcast
                                .send(crate::app::EventNotification::persisted(
                                    session.execution_id.clone(),
                                    0,
                                ));
                    }
                    _ => {}
                }
            }
        }
    }

    if global_mutated {
        state.task_queue.wake_waiters();
        return ReconcilerResult::NoAction;
    }

    ReconcilerResult::NothingFound
}

async fn run_reconciler_for_session(
    pool: &db::DbPool,
    worker_id: &str,
    session_id: &str,
    state: &AppState,
) -> ReconcilerResult {
    let mut mutated = false;
    for _pass in 0..2 {
        let session = match db::sessions::get_by_id(pool, session_id).await {
            Ok(s) => s,
            Err(_) => return ReconcilerResult::NothingFound,
        };
        if session.worker_id.as_deref() != Some(worker_id) {
            return ReconcilerResult::NothingFound;
        }

        let pending = db::sessions::count_pending_turns(pool, session_id)
            .await
            .unwrap_or(0);
        let execution = match db::executions::get_by_id(pool, &session.execution_id).await {
            Ok(e) => e,
            Err(_) => return ReconcilerResult::NothingFound,
        };

        match reconciler::reconcile(
            pool,
            &session,
            pending,
            &execution,
            true,
            Some(worker_id),
            Some(&state.supervisor),
        )
        .await
        {
            Ok(ReconcilerAction::SendCommand { token, action }) => {
                return ReconcilerResult::Command(WorkerSyncResponse::Command { token, action });
            }
            Ok(ReconcilerAction::PendingCommand) => return ReconcilerResult::PendingCommand,
            Ok(ReconcilerAction::Repaired) => {
                mutated = true;
                continue;
            }
            Ok(ReconcilerAction::Mutated) => {
                mutated = true;
                break;
            }
            Ok(ReconcilerAction::NoAction) => break,
            Err(e) => {
                tracing::warn!("reconciler error for session {}: {e}", session.id);
                break;
            }
        }
    }

    if mutated {
        state.task_queue.wake_waiters();
    }
    ReconcilerResult::NoAction
}

/// Request body for POST /api/worker/events (mid-turn message forwarding).
/// The worker sends incremental agent output here for live streaming / SSE.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerMessageRequest {
    pub worker_id: String,
    pub session_id: String,
    pub execution_id: String,
    pub msg_seq: i64,
    pub payload: serde_json::Value,
    #[serde(default)]
    pub ephemeral: bool,
}

/// Handle POST /api/worker/events — persist mid-turn message and broadcast via SSE.
pub async fn worker_event(
    State(state): State<AppState>,
    Json(request): Json<WorkerMessageRequest>,
) -> Result<StatusCode, (StatusCode, String)> {
    let session = db::sessions::get_by_id(&state.db_pool, &request.session_id)
        .await
        .map_err(|_| (StatusCode::NOT_FOUND, "session not found".to_string()))?;
    if session.worker_id.as_deref() != Some(&request.worker_id) {
        tracing::debug!(
            "stale worker_event dropped: session {} owned by {:?}, event from {}",
            request.session_id,
            session.worker_id,
            request.worker_id
        );
        state.supervisor.kill_worker(&request.worker_id).await;
        return Ok(StatusCode::OK);
    }

    let payload_str = serde_json::to_string(&request.payload)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("invalid payload: {e}")))?;

    let execution_id = session.execution_id.clone();
    if request.execution_id != execution_id {
        tracing::warn!(
            "worker_event: client execution_id {} != session.execution_id {} for session {}",
            request.execution_id,
            execution_id,
            request.session_id
        );
    }

    if request.ephemeral {
        let _ = state
            .event_broadcast
            .send(crate::app::EventNotification::ephemeral(
                execution_id,
                crate::app::EphemeralPayload {
                    session_id: request.session_id,
                    msg_seq: request.msg_seq,
                    payload: request.payload,
                },
            ));
    } else {
        let event_id = db::events::insert_with_dedup(
            &state.db_pool,
            &execution_id,
            &request.session_id,
            "message",
            &payload_str,
            request.msg_seq,
        )
        .await
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("insert event: {e}"),
            )
        })?;

        if let Some(eid) = event_id {
            let _ = state
                .event_broadcast
                .send(crate::app::EventNotification::persisted(execution_id, eid));
        }
    }

    Ok(StatusCode::CREATED)
}
