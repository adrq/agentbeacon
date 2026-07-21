//! Session state transition logic.

use crate::db::{self, DbPool};
use crate::error::SchedulerError;
use sqlx::Row;

/// Crash metadata from the worker, included in the state_change event.
#[derive(Debug, Clone, Default)]
pub struct CrashMeta {
    pub error: Option<String>,
    pub error_kind: Option<String>,
    pub stderr: Option<String>,
}

/// Actions that can be applied to a session via the transition function.
#[derive(Debug)]
pub enum Action {
    /// Set desired state. `desired_by` identifies who triggered the change.
    SetDesired(Desired, String),
    /// Enqueue a message to the session's task_queue.
    SendMessage(serde_json::Value),
    /// Create a child session and enqueue its prompt.
    Delegate(String, serde_json::Value),
    /// Worker reports executor state change.
    SetExecutorState(ExState, String, Option<CrashMeta>),
    /// Permanently fail an unrecoverable session.
    TerminateFailed(String),
    /// Scheduler detects a session crash.
    DetectCrash,
    /// Retry a crashed session.
    RetryRecovery,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Desired {
    Run,
    Stop,
    Terminate,
}

impl Desired {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Run => "run",
            Self::Stop => "stop",
            Self::Terminate => "terminate",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum ExState {
    Unassigned,
    Running,
    Idle,
    Crashed,
}

impl ExState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Unassigned => "unassigned",
            Self::Running => "running",
            Self::Idle => "idle",
            Self::Crashed => "crashed",
        }
    }
}

#[derive(Debug)]
pub enum Rejected {
    Ratchet,
    WriteBarrier,
    /// Entity not found.
    NotFound,
    /// Invalid transition (with reason).
    InvalidTransition(String),
}

impl std::fmt::Display for Rejected {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Ratchet => write!(f, "already terminated"),
            Self::WriteBarrier => write!(f, "session or execution is terminal"),
            Self::NotFound => write!(f, "not found"),
            Self::InvalidTransition(reason) => write!(f, "invalid transition: {reason}"),
        }
    }
}

pub fn is_quiescent(session: &db::sessions::Session, pending_turns: i64) -> bool {
    if pending_turns > 0 || session.command_token.is_some() {
        return false;
    }
    match session.executor_state.as_str() {
        "idle" => true,
        "unassigned" => session.agent_session_id.is_none(),
        "crashed" => session.desired == "stop",
        _ => false,
    }
}

pub fn is_active(session: &db::sessions::Session, pending_turns: i64) -> bool {
    session.executor_state == "running" || pending_turns > 0 || session.command_token.is_some()
}

pub async fn derive_execution_outcome(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    execution_id: &str,
) -> Result<String, SchedulerError> {
    let list_sql = pool.prepare_query(
        "SELECT id, parent_session_id, agent_session_id, desired, executor_state, outcome, \
         command_token, parent_notified \
         FROM sessions WHERE execution_id = ? ORDER BY created_at ASC",
    );
    let rows = sqlx::query(&list_sql)
        .bind(execution_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions for derivation: {e}")))?;

    struct SessionSlice {
        id: String,
        parent_session_id: Option<String>,
        agent_session_id: Option<String>,
        desired: String,
        executor_state: String,
        outcome: Option<String>,
        command_token: Option<String>,
        parent_notified: bool,
    }
    let sessions: Vec<SessionSlice> = rows
        .iter()
        .map(|r| SessionSlice {
            id: r.get("id"),
            parent_session_id: r.get("parent_session_id"),
            agent_session_id: r.get("agent_session_id"),
            desired: r.get("desired"),
            executor_state: r.get("executor_state"),
            outcome: r.get("outcome"),
            command_token: r.get("command_token"),
            parent_notified: r
                .try_get::<bool, _>("parent_notified")
                .unwrap_or_else(|_| r.get::<i32, _>("parent_notified") != 0),
        })
        .collect();

    let count_sql =
        pool.prepare_query("SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ?");

    for s in &sessions {
        if s.parent_session_id.is_none() && s.outcome.as_deref() == Some("failed") {
            return Ok("failed".to_string());
        }
    }

    for s in &sessions {
        let pending: i64 = sqlx::query(&count_sql)
            .bind(&s.id)
            .fetch_one(&mut **tx)
            .await
            .map(|r| r.get::<i64, _>("cnt"))
            .unwrap_or(0);
        let active = s.executor_state == "running" || pending > 0 || s.command_token.is_some();
        if active {
            return Ok("canceled".to_string());
        }
    }

    for s in &sessions {
        if s.outcome.is_none() {
            let pending: i64 = sqlx::query(&count_sql)
                .bind(&s.id)
                .fetch_one(&mut **tx)
                .await
                .map(|r| r.get::<i64, _>("cnt"))
                .unwrap_or(0);
            let quiescent = (s.executor_state == "idle"
                || (s.executor_state == "unassigned"
                    && s.agent_session_id.is_none()
                    && s.command_token.is_none())
                || (s.executor_state == "crashed" && s.desired == "stop"))
                && pending == 0
                && s.command_token.is_none();
            if !quiescent {
                return Ok("canceled".to_string());
            }
        }
    }

    let parent_outcome_sql = pool.prepare_query("SELECT outcome FROM sessions WHERE id = ?");
    for s in &sessions {
        if s.outcome.as_deref() == Some("failed")
            && !s.parent_notified
            && let Some(ref parent_id) = s.parent_session_id
            && let Ok(row) = sqlx::query(&parent_outcome_sql)
                .bind(parent_id)
                .fetch_one(&mut **tx)
                .await
        {
            let parent_outcome: Option<String> = row.get("outcome");
            if parent_outcome.is_none() {
                return Ok("canceled".to_string());
            }
        }
    }

    Ok("completed".to_string())
}

/// Apply an action to a session.
pub async fn transition(
    pool: &DbPool,
    execution_id: &str,
    session_id: &str,
    action: Action,
) -> Result<Option<i64>, Rejected> {
    let session = db::sessions::get_by_id(pool, session_id)
        .await
        .map_err(|_| Rejected::NotFound)?;

    if session.execution_id != execution_id {
        return Err(Rejected::NotFound);
    }

    match action {
        Action::SetDesired(desired, desired_by) => {
            set_desired(pool, &session, desired, &desired_by).await?;
            Ok(None)
        }
        Action::SendMessage(payload) => {
            let event_id = send_message(pool, &session, payload).await?;
            Ok(Some(event_id))
        }
        Action::Delegate(_agent_id, _prompt) => {
            let execution = db::executions::get_by_id(pool, execution_id)
                .await
                .map_err(|_| Rejected::NotFound)?;
            if execution.outcome.is_some() {
                return Err(Rejected::WriteBarrier);
            }
            Ok(None)
        }
        Action::SetExecutorState(state, ref worker_id, ref crash_meta) => {
            set_executor_state(pool, &session, state, worker_id, crash_meta.as_ref()).await?;
            Ok(None)
        }
        Action::TerminateFailed(desired_by) => {
            terminate_failed(pool, &session, &desired_by).await?;
            Ok(None)
        }
        Action::DetectCrash => {
            detect_crash(pool, &session).await?;
            Ok(None)
        }
        Action::RetryRecovery => {
            retry_recovery(pool, &session).await?;
            Ok(None)
        }
    }
}

/// SetDesired — change the desired state of a session.
async fn set_desired(
    pool: &DbPool,
    session: &db::sessions::Session,
    desired: Desired,
    desired_by: &str,
) -> Result<(), Rejected> {
    if session.desired == "terminate" {
        return Err(Rejected::Ratchet);
    }

    let desired_str = desired.as_str();

    if session.desired == desired_str {
        return Ok(());
    }

    let needs_parent_notify = desired_by == "user"
        && matches!(desired, Desired::Stop | Desired::Terminate)
        && session.parent_session_id.is_some();

    let parent_for_notify = if needs_parent_notify {
        let parent_id = session.parent_session_id.as_deref().unwrap();
        let parent = db::sessions::get_by_id(pool, parent_id).await.ok();
        let execution = db::executions::get_by_id(pool, &session.execution_id)
            .await
            .ok();
        match (&parent, &execution) {
            (Some(p), Some(e))
                if p.outcome.is_none() && e.desired != "terminate" && e.outcome.is_none() =>
            {
                Some((p.clone(), parent_id.to_string()))
            }
            _ => None,
        }
    } else {
        None
    };

    match desired {
        Desired::Terminate => {
            let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
                .await
                .map_err(|_| Rejected::InvalidTransition("begin transaction failed".into()))?;

            let tx_session = db::sessions::get_in_tx(pool, &mut tx, &session.id)
                .await
                .map_err(|_| Rejected::NotFound)?;

            if tx_session.desired == "terminate" {
                let _ = tx.rollback().await;
                return Err(Rejected::Ratchet);
            }

            let pending_sql =
                pool.prepare_query("SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ?");
            let pending: i64 = sqlx::query(&pending_sql)
                .bind(&session.id)
                .fetch_one(&mut *tx)
                .await
                .map(|r| r.get::<i64, _>("cnt"))
                .unwrap_or(0);
            let outcome = if is_quiescent(&tx_session, pending) {
                "completed"
            } else {
                "canceled"
            };

            let exec_outcome = if session.parent_session_id.is_none() {
                Some(
                    derive_execution_outcome(pool, &mut tx, &session.execution_id)
                        .await
                        .map_err(|_| {
                            Rejected::InvalidTransition("derive execution outcome failed".into())
                        })?,
                )
            } else {
                None
            };

            let sql = pool.prepare_query(
                "UPDATE sessions SET desired = 'terminate', outcome = ?, \
                 desired_by = ?, desired_at = CURRENT_TIMESTAMP, \
                 updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND desired != 'terminate'",
            );
            let result = sqlx::query(&sql)
                .bind(outcome)
                .bind(desired_by)
                .bind(&session.id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("set terminate failed".into()))?;

            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::Ratchet);
            }

            let session_event = serde_json::json!({
                "desired": "terminate",
                "outcome": outcome,
                "desired_by": desired_by,
            });
            let session_event_str = serde_json::to_string(&session_event).unwrap_or_default();
            let event_sql = pool.prepare_query(
                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                 VALUES (?, ?, 'state_change', ?) RETURNING id",
            );
            let _ = sqlx::query(&event_sql)
                .bind(&session.execution_id)
                .bind(&session.id)
                .bind(&session_event_str)
                .execute(&mut *tx)
                .await;

            if let Some(exec_outcome) = &exec_outcome {
                let exec_sql = pool.prepare_query(
                    "UPDATE executions SET desired = 'terminate', outcome = ?, \
                     updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
                     WHERE id = ? AND outcome IS NULL",
                );
                let exec_result = sqlx::query(&exec_sql)
                    .bind(exec_outcome)
                    .bind(&session.execution_id)
                    .execute(&mut *tx)
                    .await
                    .map_err(|_| Rejected::InvalidTransition("update execution failed".into()))?;

                if exec_result.rows_affected() > 0 {
                    let exec_event = serde_json::json!({
                        "desired": "terminate",
                        "outcome": exec_outcome,
                    });
                    let exec_event_str = serde_json::to_string(&exec_event).unwrap_or_default();
                    let exec_event_sql = pool.prepare_query(
                        "INSERT INTO events (execution_id, session_id, event_type, payload) \
                         VALUES (?, ?, 'state_change', ?) RETURNING id",
                    );
                    let _ = sqlx::query(&exec_event_sql)
                        .bind(&session.execution_id)
                        .bind(None::<&str>)
                        .bind(&exec_event_str)
                        .execute(&mut *tx)
                        .await;
                }
            }

            if let Some((ref parent, ref parent_id)) = parent_for_notify {
                write_parent_notification_in_tx(
                    pool,
                    &mut tx,
                    session,
                    parent,
                    parent_id,
                    "child_terminated",
                )
                .await?;
            }

            tx.commit()
                .await
                .map_err(|_| Rejected::InvalidTransition("commit transaction failed".into()))?;
        }
        Desired::Stop => {
            let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
                .await
                .map_err(|_| Rejected::InvalidTransition("begin stop tx failed".into()))?;

            let tx_session = db::sessions::get_in_tx(pool, &mut tx, &session.id)
                .await
                .map_err(|_| Rejected::NotFound)?;
            if tx_session.desired == "terminate" {
                let _ = tx.rollback().await;
                return Err(Rejected::Ratchet);
            }
            if tx_session.desired == "stop" {
                let _ = tx.rollback().await;
                return Ok(());
            }

            let set_sql = pool.prepare_query(
                "UPDATE sessions SET desired = 'stop', desired_by = ?, \
                 desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND desired = 'run'",
            );
            let result = sqlx::query(&set_sql)
                .bind(desired_by)
                .bind(&session.id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("set stop failed".into()))?;

            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::Ratchet);
            }

            let drain_sql = pool.prepare_query("DELETE FROM task_queue WHERE session_id = ?");
            sqlx::query(&drain_sql)
                .bind(&session.id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("drain queue failed".into()))?;

            let stop_event = serde_json::json!({"desired": "stop", "desired_by": desired_by});
            let event_str = serde_json::to_string(&stop_event).unwrap_or_default();
            let event_sql = pool.prepare_query(
                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                 VALUES (?, ?, 'state_change', ?)",
            );
            let _ = sqlx::query(&event_sql)
                .bind(&session.execution_id)
                .bind(&session.id)
                .bind(&event_str)
                .execute(&mut *tx)
                .await;

            if let Some((ref parent, ref parent_id)) = parent_for_notify {
                write_parent_notification_in_tx(
                    pool,
                    &mut tx,
                    session,
                    parent,
                    parent_id,
                    "child_stopped",
                )
                .await?;
            }

            tx.commit()
                .await
                .map_err(|_| Rejected::InvalidTransition("commit stop tx failed".into()))?;
        }
        Desired::Run => {
            let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
                .await
                .map_err(|_| Rejected::InvalidTransition("begin run tx failed".into()))?;

            let sql = pool.prepare_query(
                "UPDATE sessions SET desired = 'run', desired_by = ?, \
                 desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND desired != 'terminate'",
            );
            let result = sqlx::query(&sql)
                .bind(desired_by)
                .bind(&session.id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("set run failed".into()))?;

            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::Ratchet);
            }

            tx.commit()
                .await
                .map_err(|_| Rejected::InvalidTransition("commit run tx failed".into()))?;
        }
    }

    Ok(())
}

/// Write parent notification atomically inside an existing transaction.
async fn write_parent_notification_in_tx(
    pool: &db::DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    session: &db::sessions::Session,
    _parent: &db::sessions::Session,
    parent_id: &str,
    notification_type: &str,
) -> Result<(), Rejected> {
    let tx_exec = db::executions::get_in_tx(pool, tx, &session.execution_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("recheck execution failed".into()))?;
    if tx_exec.desired == "terminate" || tx_exec.outcome.is_some() {
        return Ok(());
    }

    let tx_parent = db::sessions::get_in_tx(pool, tx, parent_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("recheck parent failed".into()))?;
    if tx_parent.outcome.is_some() || tx_parent.desired == "terminate" {
        return Ok(());
    }

    if tx_parent.desired == "stop" {
        let resume_sql = pool.prepare_query(
            "UPDATE sessions SET desired = 'run', desired_by = 'system:child_action_notify', \
             desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND desired = 'stop'",
        );
        let resume_result = sqlx::query(&resume_sql)
            .bind(parent_id)
            .execute(&mut **tx)
            .await
            .map_err(|e| Rejected::InvalidTransition(format!("auto-resume parent failed: {e}")))?;

        if resume_result.rows_affected() > 0 {
            let resume_event =
                serde_json::json!({"desired": "run", "desired_by": "system:child_action_notify"});
            let resume_event_str = serde_json::to_string(&resume_event).unwrap_or_default();
            let sc_sql = pool.prepare_query(
                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                 VALUES (?, ?, 'state_change', ?)",
            );
            sqlx::query(&sc_sql)
                .bind(&session.execution_id)
                .bind(parent_id)
                .bind(&resume_event_str)
                .execute(&mut **tx)
                .await
                .map_err(|e| {
                    Rejected::InvalidTransition(format!("auto-resume state_change event: {e}"))
                })?;
        }
    }

    let notif_text = format!(
        "Child session {} {}.",
        session.id,
        notification_type.replace('_', " ")
    );
    let notification = serde_json::json!({
        "message": {
            "role": "ROLE_USER",
            "parts": [
                {"text": notif_text},
                {"data": {
                    "type": notification_type,
                    "child_session_id": &session.id,
                }}
            ]
        }
    });
    let payload_str = serde_json::to_string(&notification).unwrap_or_default();
    let source = format!("child_result:{}", session.id);
    let enqueue_sql = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
    );
    sqlx::query(&enqueue_sql)
        .bind(&session.execution_id)
        .bind(parent_id)
        .bind(&payload_str)
        .bind(&source)
        .execute(&mut **tx)
        .await
        .map_err(|e| Rejected::InvalidTransition(format!("enqueue parent notification: {e}")))?;

    let event_payload = serde_json::json!({
        "type": notification_type,
        "child_session_id": &session.id,
    });
    let event_str = serde_json::to_string(&event_payload).unwrap_or_default();
    let event_sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, 'platform', ?)",
    );
    sqlx::query(&event_sql)
        .bind(&session.execution_id)
        .bind(parent_id)
        .bind(&event_str)
        .execute(&mut **tx)
        .await
        .map_err(|e| Rejected::InvalidTransition(format!("parent platform event: {e}")))?;

    Ok(())
}

/// SendMessage — enqueue a message to the session's task_queue.
/// If the session is stopped, atomically resume it.
async fn send_message(
    pool: &DbPool,
    session: &db::sessions::Session,
    payload: serde_json::Value,
) -> Result<i64, Rejected> {
    if session.desired == "terminate" || session.outcome.is_some() {
        return Err(Rejected::WriteBarrier);
    }

    let execution = db::executions::get_by_id(pool, &session.execution_id)
        .await
        .map_err(|_| Rejected::NotFound)?;
    if execution.desired == "terminate" || execution.outcome.is_some() {
        return Err(Rejected::WriteBarrier);
    }

    let payload_json = serde_json::to_string(&payload)
        .map_err(|_| Rejected::InvalidTransition("serialize payload failed".into()))?;

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("begin tx failed".into()))?;

    let tx_session = db::sessions::get_in_tx(pool, &mut tx, &session.id)
        .await
        .map_err(|_| Rejected::NotFound)?;
    if tx_session.desired == "terminate" || tx_session.outcome.is_some() {
        let _ = tx.rollback().await;
        return Err(Rejected::WriteBarrier);
    }

    let tx_execution = db::executions::get_in_tx(pool, &mut tx, &session.execution_id)
        .await
        .map_err(|_| Rejected::NotFound)?;
    if tx_execution.desired == "terminate" || tx_execution.outcome.is_some() {
        let _ = tx.rollback().await;
        return Err(Rejected::WriteBarrier);
    }

    if tx_session.desired == "stop" {
        let sql = pool.prepare_query(
            "UPDATE sessions SET desired = 'run', desired_by = 'user:send_message', \
             desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND desired = 'stop'",
        );
        sqlx::query(&sql)
            .bind(&session.id)
            .execute(&mut *tx)
            .await
            .map_err(|_| Rejected::InvalidTransition("auto-resume failed".into()))?;
    }

    let insert_sql = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
    );
    sqlx::query(&insert_sql)
        .bind(&session.execution_id)
        .bind(&session.id)
        .bind(&payload_json)
        .bind(None::<&str>)
        .execute(&mut *tx)
        .await
        .map_err(|_| Rejected::InvalidTransition("enqueue message failed".into()))?;

    let event_payload_str = if let Some(event_msg) = payload.get("event_message") {
        serde_json::to_string(event_msg).unwrap_or_else(|_| payload_json.clone())
    } else if let Some(msg) = payload.get("message") {
        serde_json::to_string(msg).unwrap_or_else(|_| payload_json.clone())
    } else {
        payload_json.clone()
    };
    let event_sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) \
         VALUES (?, ?, 'message', ?) RETURNING id",
    );
    let event_id: i64 = sqlx::query(&event_sql)
        .bind(&session.execution_id)
        .bind(&session.id)
        .bind(&event_payload_str)
        .fetch_one(&mut *tx)
        .await
        .map_err(|_| Rejected::InvalidTransition("insert message event failed".into()))?
        .try_get("id")
        .unwrap_or(0);

    tx.commit()
        .await
        .map_err(|_| Rejected::InvalidTransition("commit tx failed".into()))?;

    Ok(event_id)
}

/// Worker reports executor state change. Guarded by worker_id ownership.
async fn set_executor_state(
    pool: &DbPool,
    session: &db::sessions::Session,
    state: ExState,
    worker_id: &str,
    crash_meta: Option<&CrashMeta>,
) -> Result<(), Rejected> {
    if session.outcome.is_some() && session.command_token.is_none() {
        return Err(Rejected::WriteBarrier);
    }

    let state_str = state.as_str();

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("begin executor state tx failed".into()))?;

    match state {
        ExState::Crashed => {
            let sql = pool.prepare_query(
                "UPDATE sessions SET executor_state = 'crashed', \
                 command_token = NULL, command_type = NULL, command_at = NULL, \
                 command_has_payload = FALSE, \
                 updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND worker_id = ? \
                 AND (outcome IS NULL OR command_token IS NOT NULL)",
            );
            let result = sqlx::query(&sql)
                .bind(&session.id)
                .bind(worker_id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("set crashed failed".into()))?;
            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::WriteBarrier);
            }
            if session.command_has_payload {
                let event = serde_json::json!({
                    "message": "Agent recovered from a crash. A message may have been lost."
                });
                let payload_str = serde_json::to_string(&event).unwrap_or_default();
                let evt_sql = pool.prepare_query(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) \
                     VALUES (?, ?, 'platform', ?)",
                );
                let _ = sqlx::query(&evt_sql)
                    .bind(&session.execution_id)
                    .bind(&session.id)
                    .bind(&payload_str)
                    .execute(&mut *tx)
                    .await;
            }
            if session.executor_state != "crashed" {
                let mut event_data = serde_json::json!({"executor_state": "crashed"});
                if let Some(meta) = crash_meta {
                    if let Some(ref err) = meta.error {
                        event_data["error"] = serde_json::json!(err);
                    }
                    if let Some(ref ek) = meta.error_kind {
                        event_data["error_kind"] = serde_json::json!(ek);
                    }
                    if let Some(ref stderr) = meta.stderr {
                        event_data["stderr"] = serde_json::json!(stderr);
                    }
                }
                let sc_sql = pool.prepare_query(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) \
                     VALUES (?, ?, 'state_change', ?)",
                );
                let _ = sqlx::query(&sc_sql)
                    .bind(&session.execution_id)
                    .bind(&session.id)
                    .bind(serde_json::to_string(&event_data).unwrap_or_default())
                    .execute(&mut *tx)
                    .await;
            }
        }
        ExState::Idle => {
            let sql = if session.recovery_attempts > 0 {
                pool.prepare_query(
                    "UPDATE sessions SET executor_state = 'idle', \
                     recovery_attempts = 0, updated_at = CURRENT_TIMESTAMP \
                     WHERE id = ? AND worker_id = ? \
                     AND (outcome IS NULL OR command_token IS NOT NULL)",
                )
            } else {
                pool.prepare_query(
                    "UPDATE sessions SET executor_state = 'idle', \
                     updated_at = CURRENT_TIMESTAMP \
                     WHERE id = ? AND worker_id = ? \
                     AND (outcome IS NULL OR command_token IS NOT NULL)",
                )
            };
            let result = sqlx::query(&sql)
                .bind(&session.id)
                .bind(worker_id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("set idle failed".into()))?;
            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::WriteBarrier);
            }
            if session.executor_state != "idle" {
                let idle_event = serde_json::json!({"executor_state": "idle"});
                let sc_sql = pool.prepare_query(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) \
                     VALUES (?, ?, 'state_change', ?)",
                );
                let _ = sqlx::query(&sc_sql)
                    .bind(&session.execution_id)
                    .bind(&session.id)
                    .bind(serde_json::to_string(&idle_event).unwrap_or_default())
                    .execute(&mut *tx)
                    .await;
            }
        }
        ExState::Running => {
            let sql = pool.prepare_query(
                "UPDATE sessions SET executor_state = 'running', updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND worker_id = ? \
                 AND (outcome IS NULL OR command_token IS NOT NULL)",
            );
            let result = sqlx::query(&sql)
                .bind(&session.id)
                .bind(worker_id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition("set running failed".into()))?;
            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::WriteBarrier);
            }
            if session.executor_state != "running" {
                let running_event = serde_json::json!({"executor_state": "running"});
                let sc_sql = pool.prepare_query(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) \
                     VALUES (?, ?, 'state_change', ?)",
                );
                let _ = sqlx::query(&sc_sql)
                    .bind(&session.execution_id)
                    .bind(&session.id)
                    .bind(serde_json::to_string(&running_event).unwrap_or_default())
                    .execute(&mut *tx)
                    .await;
            }
        }
        _ => {
            let sql = pool.prepare_query(
                "UPDATE sessions SET executor_state = ?, updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND worker_id = ? \
                 AND (outcome IS NULL OR command_token IS NOT NULL)",
            );
            let result = sqlx::query(&sql)
                .bind(state_str)
                .bind(&session.id)
                .bind(worker_id)
                .execute(&mut *tx)
                .await
                .map_err(|_| Rejected::InvalidTransition(format!("set {state_str} failed")))?;
            if result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(Rejected::WriteBarrier);
            }
        }
    }

    tx.commit()
        .await
        .map_err(|_| Rejected::InvalidTransition("commit executor state tx failed".into()))?;

    Ok(())
}

/// TerminateFailed — atomically set desired=terminate + outcome=failed.
async fn terminate_failed(
    pool: &DbPool,
    session: &db::sessions::Session,
    desired_by: &str,
) -> Result<(), Rejected> {
    if session.desired == "terminate" {
        return Err(Rejected::Ratchet);
    }

    let exec_outcome: Option<String> = if session.parent_session_id.is_none() {
        Some("failed".to_string())
    } else {
        None
    };

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("begin transaction failed".into()))?;

    let sql = pool.prepare_query(
        "UPDATE sessions SET desired = 'terminate', outcome = 'failed', \
         desired_by = ?, desired_at = CURRENT_TIMESTAMP, \
         updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND desired != 'terminate'",
    );
    let result = sqlx::query(&sql)
        .bind(desired_by)
        .bind(&session.id)
        .execute(&mut *tx)
        .await
        .map_err(|_| Rejected::InvalidTransition("terminate failed failed".into()))?;

    if result.rows_affected() == 0 {
        let _ = tx.rollback().await;
        return Err(Rejected::Ratchet);
    }

    let session_event = serde_json::json!({
        "desired": "terminate",
        "outcome": "failed",
        "desired_by": desired_by,
    });
    let session_event_str = serde_json::to_string(&session_event).unwrap_or_default();
    let event_sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) \
         VALUES (?, ?, 'state_change', ?) RETURNING id",
    );
    let _ = sqlx::query(&event_sql)
        .bind(&session.execution_id)
        .bind(&session.id)
        .bind(&session_event_str)
        .execute(&mut *tx)
        .await;

    if let Some(exec_outcome) = &exec_outcome {
        let exec_sql = pool.prepare_query(
            "UPDATE executions SET desired = 'terminate', outcome = ?, \
             updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND outcome IS NULL",
        );
        let exec_result = sqlx::query(&exec_sql)
            .bind(exec_outcome)
            .bind(&session.execution_id)
            .execute(&mut *tx)
            .await
            .map_err(|_| Rejected::InvalidTransition("update execution failed".into()))?;

        if exec_result.rows_affected() > 0 {
            let exec_event = serde_json::json!({
                "desired": "terminate",
                "outcome": exec_outcome,
            });
            let exec_event_str = serde_json::to_string(&exec_event).unwrap_or_default();
            let exec_event_sql = pool.prepare_query(
                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                 VALUES (?, ?, 'state_change', ?) RETURNING id",
            );
            let _ = sqlx::query(&exec_event_sql)
                .bind(&session.execution_id)
                .bind(None::<&str>)
                .bind(&exec_event_str)
                .execute(&mut *tx)
                .await;
        }
    }

    tx.commit()
        .await
        .map_err(|_| Rejected::InvalidTransition("commit transaction failed".into()))?;

    Ok(())
}

/// DetectCrash — scheduler detects crash (supervisor/heartbeat/command timeout).
/// Sets executor_state=crashed, clears worker_id and command fields.
async fn detect_crash(pool: &DbPool, session: &db::sessions::Session) -> Result<(), Rejected> {
    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("begin detect_crash tx failed".into()))?;

    let sql = pool.prepare_query(
        "UPDATE sessions SET executor_state = 'crashed', worker_id = NULL, \
         command_token = NULL, command_type = NULL, command_at = NULL, \
         command_has_payload = FALSE, \
         updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND (worker_id IS NOT NULL OR command_token IS NOT NULL)",
    );
    let result = sqlx::query(&sql)
        .bind(&session.id)
        .execute(&mut *tx)
        .await
        .map_err(|_| Rejected::InvalidTransition("detect crash failed".into()))?;

    if result.rows_affected() == 0 {
        let _ = tx.rollback().await;
        return Err(Rejected::WriteBarrier);
    }

    if session.executor_state != "crashed" {
        let event_data = serde_json::json!({"executor_state": "crashed"});
        let sc_sql = pool.prepare_query(
            "INSERT INTO events (execution_id, session_id, event_type, payload) \
             VALUES (?, ?, 'state_change', ?)",
        );
        let _ = sqlx::query(&sc_sql)
            .bind(&session.execution_id)
            .bind(&session.id)
            .bind(serde_json::to_string(&event_data).unwrap_or_default())
            .execute(&mut *tx)
            .await;
    }

    tx.commit()
        .await
        .map_err(|_| Rejected::InvalidTransition("commit detect_crash tx failed".into()))?;

    Ok(())
}

/// Increment recovery_attempts and set executor_state=unassigned.
async fn retry_recovery(pool: &DbPool, session: &db::sessions::Session) -> Result<(), Rejected> {
    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|_| Rejected::InvalidTransition("begin retry_recovery tx failed".into()))?;

    let sql = pool.prepare_query(
        "UPDATE sessions SET executor_state = 'unassigned', \
         recovery_attempts = recovery_attempts + 1, \
         updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND executor_state = 'crashed' AND outcome IS NULL",
    );
    let result = sqlx::query(&sql)
        .bind(&session.id)
        .execute(&mut *tx)
        .await
        .map_err(|_| Rejected::InvalidTransition("retry recovery failed".into()))?;

    if result.rows_affected() == 0 {
        let _ = tx.rollback().await;
        return Err(Rejected::WriteBarrier);
    }

    if session.agent_session_id.is_some() {
        let event = serde_json::json!({
            "message": "Agent recovered from a crash; work in progress may have been lost."
        });
        let evt_sql = pool.prepare_query(
            "INSERT INTO events (execution_id, session_id, event_type, payload) \
             VALUES (?, ?, 'platform', ?)",
        );
        let _ = sqlx::query(&evt_sql)
            .bind(&session.execution_id)
            .bind(&session.id)
            .bind(serde_json::to_string(&event).unwrap_or_default())
            .execute(&mut *tx)
            .await;
    }

    tx.commit()
        .await
        .map_err(|_| Rejected::InvalidTransition("commit retry_recovery tx failed".into()))?;

    Ok(())
}
