use std::sync::Arc;

use serde::Serialize;
use sqlx::Row;

use crate::db::{self, DbPool};
use crate::error::SchedulerError;
use crate::services::transition;
use crate::supervisor::Supervisor;

/// Action produced for a session.
#[derive(Debug)]
#[allow(clippy::large_enum_variant)]
pub enum ReconcilerAction {
    NoAction,
    /// A command is pending — caller should return without long-polling
    PendingCommand,
    SendCommand {
        token: String,
        action: CommandAction,
    },
    /// Inconsistency corrected.
    Repaired,
    /// State was mutated.
    Mutated,
}

/// Commands issued to workers.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(clippy::large_enum_variant)]
pub enum CommandAction {
    Assign {
        session_id: String,
        execution_id: String,
        payload: Option<serde_json::Value>,
        resume: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        cwd: Option<String>,
        driver: serde_json::Value,
        /// Fully composed runtime config including AgentBeacon briefing and
        /// effective system_prompt.
        agent_config: serde_json::Value,
        #[serde(skip_serializing_if = "Option::is_none")]
        agent_session_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        project_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        mcp_servers: Option<serde_json::Value>,
        /// Next msg_seq for message dedup.
        #[serde(default)]
        next_msg_seq: i64,
    },
    FeedTurn {
        session_id: String,
        payload: serde_json::Value,
    },
    StopTurn {
        session_id: String,
    },
    Cancel {
        session_id: String,
    },
}

const MAX_RECOVERY_ATTEMPTS: i64 = 2;

pub async fn reconcile(
    pool: &DbPool,
    session: &db::sessions::Session,
    pending_turns: i64,
    execution: &db::Execution,
    command_ok: bool,
    worker_id: Option<&str>,
    supervisor: Option<&Arc<Supervisor>>,
) -> Result<ReconcilerAction, SchedulerError> {
    if session.desired == "terminate" && session.outcome.is_none() {
        if session.parent_session_id.is_none() {
            let mut repair_tx =
                db::executions::begin_execution_tx(pool, &session.execution_id).await?;

            let tx_session = db::sessions::get_in_tx(pool, &mut repair_tx, &session.id)
                .await
                .map_err(|e| SchedulerError::Database(format!("repair re-read session: {e}")))?;
            if tx_session.desired != "terminate" || tx_session.outcome.is_some() {
                let _ = repair_tx.rollback().await;
                return Ok(ReconcilerAction::NoAction);
            }
            let tx_pending_sql =
                pool.prepare_query("SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ?");
            let tx_pending: i64 = sqlx::query(&tx_pending_sql)
                .bind(&session.id)
                .fetch_one(&mut **repair_tx)
                .await
                .map(|r| r.get::<i64, _>("cnt"))
                .map_err(|e| SchedulerError::Database(format!("count pending turns: {e}")))?;
            let outcome = if transition::is_quiescent(&tx_session, tx_pending) {
                "completed"
            } else {
                "canceled"
            };

            let exec_outcome = if execution.outcome.is_none() {
                Some(
                    transition::derive_execution_outcome(
                        pool,
                        &mut repair_tx,
                        &session.execution_id,
                    )
                    .await?,
                )
            } else {
                None
            };

            let sql = pool.prepare_query(
                "UPDATE sessions SET outcome = ?, \
                 desired_by = COALESCE(desired_by, 'system:repair'), \
                 desired_at = COALESCE(desired_at, CURRENT_TIMESTAMP), \
                 updated_at = CURRENT_TIMESTAMP, \
                 completed_at = CURRENT_TIMESTAMP WHERE id = ? AND outcome IS NULL",
            );
            sqlx::query(&sql)
                .bind(outcome)
                .bind(&session.id)
                .execute(&mut **repair_tx)
                .await
                .map_err(|e| SchedulerError::Database(format!("repair outcome failed: {e}")))?;

            if let Some(ref exec_outcome) = exec_outcome {
                transition::write_execution_terminal_in_tx(
                    pool,
                    &mut repair_tx,
                    &session.execution_id,
                    exec_outcome,
                )
                .await?;
            }

            repair_tx
                .commit()
                .await
                .map_err(|e| SchedulerError::Database(format!("commit repair tx: {e}")))?;
        } else {
            let mut repair_tx =
                db::executions::begin_execution_tx(pool, &session.execution_id).await?;
            let repaired = async {
                let current = db::sessions::get_in_tx(pool, &mut repair_tx, &session.id).await?;
                if current.outcome.is_some() || current.desired != "terminate" {
                    return Ok(());
                }
                let count_sql = pool
                    .prepare_query("SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ?");
                let tx_pending: i64 = sqlx::query(&count_sql)
                    .bind(&current.id)
                    .fetch_one(&mut **repair_tx)
                    .await
                    .map(|r| r.get::<i64, _>("cnt"))
                    .map_err(|e| SchedulerError::Database(format!("repair pending count: {e}")))?;
                let outcome = if transition::is_quiescent(&current, tx_pending) {
                    "completed"
                } else {
                    "canceled"
                };
                let sql = pool.prepare_query(
                    "UPDATE sessions SET outcome = ?, \
                     desired_by = COALESCE(desired_by, 'system:repair'), \
                     desired_at = COALESCE(desired_at, CURRENT_TIMESTAMP), \
                     updated_at = CURRENT_TIMESTAMP, \
                     completed_at = CURRENT_TIMESTAMP \
                     WHERE id = ? AND outcome IS NULL AND desired = 'terminate' \
                     AND executor_state = ?",
                );
                sqlx::query(&sql)
                    .bind(outcome)
                    .bind(&current.id)
                    .bind(&current.executor_state)
                    .execute(&mut **repair_tx)
                    .await
                    .map_err(|e| SchedulerError::Database(format!("repair outcome failed: {e}")))?;
                Ok::<(), SchedulerError>(())
            }
            .await;
            match repaired {
                Ok(()) => repair_tx
                    .commit()
                    .await
                    .map_err(|e| SchedulerError::Database(format!("commit repair tx: {e}")))?,
                Err(e) => {
                    let _ = repair_tx.rollback().await;
                    return Err(e);
                }
            }
        }
        return Ok(ReconcilerAction::Repaired);
    }

    if session.outcome.is_some() && session.desired != "terminate" {
        let mut repair_tx = db::executions::begin_execution_tx(pool, &session.execution_id).await?;
        let repair = async {
            let current = db::sessions::get_in_tx(pool, &mut repair_tx, &session.id).await?;
            if current.outcome.is_none() || current.desired == "terminate" {
                return Ok(());
            }
            let sql = pool.prepare_query(
                "UPDATE sessions SET desired = 'terminate', \
                 desired_by = COALESCE(desired_by, 'system:repair'), \
                 desired_at = COALESCE(desired_at, CURRENT_TIMESTAMP), \
                 updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND desired != 'terminate' AND outcome IS NOT NULL",
            );
            let repaired = sqlx::query(&sql)
                .bind(&session.id)
                .execute(&mut **repair_tx)
                .await
                .map_err(|e| SchedulerError::Database(format!("repair desired failed: {e}")))?;
            if repaired.rows_affected() == 0 {
                return Ok(());
            }

            let tx_exec =
                db::executions::get_in_tx(pool, &mut repair_tx, &session.execution_id).await?;
            if current.parent_session_id.is_none() && tx_exec.outcome.is_none() {
                let derived = transition::derive_execution_outcome(
                    pool,
                    &mut repair_tx,
                    &session.execution_id,
                )
                .await?;
                transition::write_execution_terminal_in_tx(
                    pool,
                    &mut repair_tx,
                    &session.execution_id,
                    &derived,
                )
                .await?;
            }
            Ok(())
        }
        .await;
        match repair {
            Ok(()) => repair_tx
                .commit()
                .await
                .map_err(|e| SchedulerError::Database(format!("commit repair tx: {e}")))?,
            Err(e) => {
                let _ = repair_tx.rollback().await;
                return Err(e);
            }
        }
        return Ok(ReconcilerAction::Repaired);
    }

    if session.parent_session_id.is_none()
        && session.outcome.is_some()
        && execution.outcome.is_none()
    {
        let mut repair_tx = db::executions::begin_execution_tx(pool, &session.execution_id).await?;
        let tx_root = db::sessions::get_in_tx(pool, &mut repair_tx, &session.id)
            .await
            .map_err(|e| SchedulerError::Database(format!("repair re-read root: {e}")))?;
        if tx_root.outcome.is_none() {
            let _ = repair_tx.rollback().await;
            return Ok(ReconcilerAction::NoAction);
        }
        let exec_outcome =
            transition::derive_execution_outcome(pool, &mut repair_tx, &session.execution_id)
                .await?;
        transition::write_execution_terminal_in_tx(
            pool,
            &mut repair_tx,
            &session.execution_id,
            &exec_outcome,
        )
        .await?;
        repair_tx
            .commit()
            .await
            .map_err(|e| SchedulerError::Database(format!("commit repair tx: {e}")))?;
        return Ok(ReconcilerAction::Repaired);
    }

    if session.desired == "terminate" {
        let children = db::sessions::get_children(pool, &session.id).await?;
        for child in &children {
            if child.outcome.is_none() && child.desired != "terminate" {
                let _ = transition::transition(
                    pool,
                    &session.execution_id,
                    &child.id,
                    transition::Action::SetDesired(
                        transition::Desired::Terminate,
                        "system:cascade".to_string(),
                    ),
                )
                .await;
            }
        }
    }

    if session.command_token.is_some() {
        let cmd_type = session.command_type.as_deref().unwrap_or("");

        if session.executor_state == "crashed" {
            let mut repair_tx = db::executions::begin_execution_tx(pool, &session.execution_id)
                .await
                .map_err(|e| SchedulerError::Database(format!("begin command repair tx: {e}")))?;
            let clear_sql = pool.prepare_query(
                "UPDATE sessions SET command_token = NULL, command_type = NULL, \
                 command_at = NULL, command_has_payload = FALSE, \
                 updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND executor_state = 'crashed'",
            );
            let result = sqlx::query(&clear_sql)
                .bind(&session.id)
                .execute(&mut **repair_tx)
                .await
                .map_err(|e| SchedulerError::Database(format!("clear stale command: {e}")))?;
            if result.rows_affected() == 0 {
                let _ = repair_tx.rollback().await;
                return Ok(ReconcilerAction::NoAction);
            }
            repair_tx
                .commit()
                .await
                .map_err(|e| SchedulerError::Database(format!("commit command repair: {e}")))?;
            return Ok(ReconcilerAction::Repaired);
        }

        if let Some(ref command_at) = session.command_at {
            let timeout_secs = match cmd_type {
                "assign" => 60,
                "feed_turn" => 5,
                "stop_turn" | "cancel" => 15,
                _ => 60,
            };
            let elapsed = chrono::Utc::now() - *command_at;
            {
                if elapsed.num_seconds() > timeout_secs {
                    if session.command_has_payload {
                        let event_payload = serde_json::json!({
                            "message": "Agent recovered from a crash. A message may have been lost."
                        });
                        let _ = db::events::insert_locked(
                            pool,
                            &session.execution_id,
                            Some(&session.id),
                            "platform",
                            &serde_json::to_string(&event_payload).unwrap_or_default(),
                        )
                        .await;
                    }
                    if let Some(ref wid) = session.worker_id
                        && let Some(sup) = supervisor
                    {
                        sup.kill_worker(wid).await;
                    }
                    let _ = transition::transition(
                        pool,
                        &session.execution_id,
                        &session.id,
                        transition::Action::DetectCrash,
                    )
                    .await;
                    return Ok(ReconcilerAction::Mutated);
                }
            }
        }

        if command_ok {
            let needed_cmd = match (session.desired.as_str(), session.executor_state.as_str()) {
                ("terminate", _) => Some("cancel"),
                ("stop", "running") => Some("stop_turn"),
                ("stop", _) if cmd_type == "assign" || cmd_type == "feed_turn" => Some("stop_turn"),
                _ => None,
            };
            if let Some(needed) = needed_cmd {
                let cmd_priority = |c: &str| -> u8 {
                    match c {
                        "cancel" => 3,
                        "stop_turn" => 2,
                        _ => 1,
                    }
                };
                if cmd_priority(needed) > cmd_priority(cmd_type) {
                    match needed {
                        "stop_turn" => return issue_stop_turn(pool, session).await,
                        "cancel" => return issue_cancel(pool, session).await,
                        _ => {}
                    }
                }
            }
        }

        return Ok(ReconcilerAction::PendingCommand);
    }

    let exec_alive = execution.desired == "run" && execution.outcome.is_none();

    match (session.desired.as_str(), session.executor_state.as_str()) {
        ("run", "unassigned") => {
            if is_agent_deleted(pool, &session.agent_id).await {
                terminate_and_cascade(pool, session, "system:agent_deleted").await?;
                return Ok(ReconcilerAction::Mutated);
            }
            if (pending_turns > 0 || session.agent_session_id.is_some()) && command_ok && exec_alive
            {
                let resume = session.agent_session_id.is_some();
                return issue_assign(pool, session, resume, worker_id).await;
            }
            Ok(ReconcilerAction::NoAction)
        }

        ("run", "running") => {
            if pending_turns > 0 && command_ok && exec_alive {
                return issue_feed_turn(pool, session).await;
            }
            Ok(ReconcilerAction::NoAction)
        }

        ("run", "idle") => {
            if pending_turns > 0 && command_ok && exec_alive {
                return issue_feed_turn(pool, session).await;
            }
            Ok(ReconcilerAction::NoAction)
        }

        ("run", "crashed") => {
            if is_agent_deleted(pool, &session.agent_id).await {
                terminate_and_cascade(pool, session, "system:agent_deleted").await?;
                return Ok(ReconcilerAction::Mutated);
            }
            if recoverable(pool, session, pending_turns).await? {
                transition::transition(
                    pool,
                    &session.execution_id,
                    &session.id,
                    transition::Action::RetryRecovery,
                )
                .await
                .map_err(|e| SchedulerError::Database(format!("retry recovery failed: {e}")))?;
                Ok(ReconcilerAction::Mutated)
            } else {
                match transition::terminate_session_failed(
                    pool,
                    &session.execution_id,
                    &session.id,
                    "system:crash_unrecoverable",
                    true,
                )
                .await
                {
                    Ok(()) => {}
                    Err(transition::Rejected::Ratchet) => {
                        return Ok(ReconcilerAction::NoAction);
                    }
                    Err(e) => {
                        return Err(SchedulerError::Database(format!(
                            "terminate failed failed: {e:?}"
                        )));
                    }
                };
                let event = serde_json::json!({
                    "message": "Agent session failed permanently. Work or messages may have been lost."
                });
                let _ = db::events::insert_locked(
                    pool,
                    &session.execution_id,
                    Some(&session.id),
                    "platform",
                    &serde_json::to_string(&event).unwrap_or_default(),
                )
                .await;
                let fresh = db::sessions::get_by_id(pool, &session.id).await?;
                cascade_children(pool, &fresh).await?;
                Ok(ReconcilerAction::Mutated)
            }
        }

        ("stop", "running") => {
            if command_ok {
                issue_stop_turn(pool, session).await
            } else {
                Ok(ReconcilerAction::NoAction)
            }
        }

        ("stop", "idle") => Ok(ReconcilerAction::NoAction),

        ("stop", "unassigned") => Ok(ReconcilerAction::NoAction),

        ("stop", "crashed") => Ok(ReconcilerAction::NoAction),

        ("terminate", "running") => {
            if command_ok {
                issue_cancel(pool, session).await
            } else {
                Ok(ReconcilerAction::NoAction)
            }
        }

        ("terminate", "idle") => {
            if session.worker_id.is_some() {
                if command_ok {
                    issue_cancel(pool, session).await
                } else {
                    Ok(ReconcilerAction::NoAction)
                }
            } else {
                finalize(pool, session).await?;
                Ok(ReconcilerAction::Mutated)
            }
        }

        ("terminate", "unassigned") => {
            finalize(pool, session).await?;
            Ok(ReconcilerAction::Mutated)
        }

        ("terminate", "crashed") => {
            finalize(pool, session).await?;
            Ok(ReconcilerAction::Mutated)
        }

        _ => Ok(ReconcilerAction::NoAction),
    }
}

/// Check if an agent's config has been deleted.
async fn is_agent_deleted(pool: &DbPool, agent_id: &str) -> bool {
    match db::agents::get_by_id(pool, agent_id).await {
        Ok(agent) => agent.deleted_at.is_some(),
        Err(SchedulerError::NotFound(_)) => true,
        Err(_) => false,
    }
}

/// Check if a crashed session is recoverable.
async fn recoverable(
    pool: &DbPool,
    session: &db::sessions::Session,
    pending_turns: i64,
) -> Result<bool, SchedulerError> {
    if session.recovery_attempts >= MAX_RECOVERY_ATTEMPTS {
        return Ok(false);
    }

    let execution = db::executions::get_by_id(pool, &session.execution_id).await?;
    if execution.outcome.is_some() {
        return Ok(false);
    }

    let agent = db::agents::get_by_id(pool, &session.agent_id).await?;
    if agent.deleted_at.is_some() {
        return Ok(false);
    }

    if session.agent_session_id.is_some() {
        let is_resumable = matches!(
            agent.agent_type.as_str(),
            "claude_sdk" | "copilot_sdk" | "codex_sdk"
        );
        return Ok(is_resumable && session.cwd.is_some());
    }

    Ok(pending_turns > 0)
}

/// Issue an assign command.
async fn issue_assign(
    pool: &DbPool,
    session: &db::sessions::Session,
    resume: bool,
    worker_id: Option<&str>,
) -> Result<ReconcilerAction, SchedulerError> {
    let token = uuid::Uuid::new_v4().to_string();

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let agent = db::agents::get_by_id_in_tx(pool, &mut tx, &session.agent_id).await?;
    let driver = build_driver_info_in_tx(&agent, session, pool, &mut tx).await?;

    let tx_exec = db::executions::get_in_tx(pool, &mut tx, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("execution recheck failed: {e}")))?;
    if tx_exec.desired == "terminate" || tx_exec.outcome.is_some() {
        let _ = tx.rollback().await;
        return Ok(ReconcilerAction::NoAction);
    }

    let select_query = if pool.is_postgres() {
        "SELECT id, execution_id, session_id, task_payload \
         FROM task_queue WHERE session_id = $1 \
         ORDER BY queued_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
    } else {
        "SELECT id, execution_id, session_id, task_payload \
         FROM task_queue WHERE session_id = ? \
         ORDER BY queued_at ASC LIMIT 1"
    };
    let row = sqlx::query(select_query)
        .bind(&session.id)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("select task failed: {e}")))?;

    let task = if let Some(row) = row {
        let row_id: i64 = row
            .try_get("id")
            .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?;
        let payload_json: String = row
            .try_get("task_payload")
            .map_err(|e| SchedulerError::Database(format!("get task_payload failed: {e}")))?;
        let task_payload: serde_json::Value = serde_json::from_str(&payload_json).map_err(|e| {
            SchedulerError::Database(format!("deserialize task_payload failed: {e}"))
        })?;

        let delete_query = if pool.is_postgres() {
            "DELETE FROM task_queue WHERE id = $1"
        } else {
            "DELETE FROM task_queue WHERE id = ?"
        };
        sqlx::query(delete_query)
            .bind(row_id)
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("delete task failed: {e}")))?;

        Some(task_payload)
    } else {
        None
    };

    if !resume && task.is_none() {
        let _ = tx.rollback().await;
        return Ok(ReconcilerAction::NoAction);
    }

    let has_payload = task.is_some();

    let sql = if worker_id.is_some() {
        pool.prepare_query(
            "UPDATE sessions SET command_token = ?, command_type = 'assign', \
             command_at = CURRENT_TIMESTAMP, command_has_payload = ?, \
             worker_id = ?, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND command_token IS NULL AND desired = 'run' \
             AND executor_state = 'unassigned'",
        )
    } else {
        pool.prepare_query(
            "UPDATE sessions SET command_token = ?, command_type = 'assign', \
             command_at = CURRENT_TIMESTAMP, command_has_payload = ?, \
             updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND command_token IS NULL AND desired = 'run' \
             AND executor_state = 'unassigned'",
        )
    };
    let result = if let Some(wid) = worker_id {
        sqlx::query(&sql)
            .bind(&token)
            .bind(has_payload)
            .bind(wid)
            .bind(&session.id)
            .execute(&mut **tx)
            .await
    } else {
        sqlx::query(&sql)
            .bind(&token)
            .bind(has_payload)
            .bind(&session.id)
            .execute(&mut **tx)
            .await
    }
    .map_err(|e| SchedulerError::Database(format!("issue assign failed: {e}")))?;

    if result.rows_affected() == 0 {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
        return Ok(ReconcilerAction::NoAction);
    }

    let briefing_ctx = crate::services::agent_config::briefing_context_for_session_in_tx(
        pool, &mut tx, session, &agent, &tx_exec,
    )
    .await?;
    let effective_agent_config = crate::services::agent_config::compose_agent_config_in_tx(
        pool,
        &mut tx,
        &agent,
        &briefing_ctx,
    )
    .await;

    let next_msg_seq = {
        let seq_sql = pool.prepare_query(
            "SELECT COALESCE(MAX(msg_seq), -1) + 1 as next_seq FROM events WHERE session_id = ?",
        );
        sqlx::query(&seq_sql)
            .bind(&session.id)
            .fetch_one(&mut **tx)
            .await
            .ok()
            .and_then(|row| row.try_get::<i64, _>("next_seq").ok())
            .unwrap_or(0)
    };

    let mcp_servers = if let Some(ref pid) = tx_exec.project_id {
        let mcp_sql = pool.prepare_query(
            "SELECT m.id as mcp_server_id, m.name, m.transport_type, m.config \
             FROM project_mcp_servers pm \
             JOIN mcp_servers m ON pm.mcp_server_id = m.id \
             WHERE pm.project_id = ? \
             ORDER BY m.name",
        );
        let rows = sqlx::query(&mcp_sql)
            .bind(pid)
            .fetch_all(&mut **tx)
            .await
            .map_err(|e| {
                SchedulerError::Database(format!("list project MCP servers failed: {e}"))
            })?;
        if rows.is_empty() {
            None
        } else {
            let servers: Vec<db::project_mcp_servers::McpServerPoolEntry> = rows
                .iter()
                .map(|r| {
                    let config_str: String = r.get::<String, _>("config");
                    db::project_mcp_servers::McpServerPoolEntry {
                        mcp_server_id: r.get("mcp_server_id"),
                        name: r.get("name"),
                        transport_type: r.get("transport_type"),
                        config: serde_json::from_str(&config_str).unwrap_or_default(),
                    }
                })
                .collect();
            Some(crate::services::mcp::build_mcp_servers_payload(&servers))
        }
    } else {
        None
    };

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit failed: {e}")))?;

    Ok(ReconcilerAction::SendCommand {
        token,
        action: CommandAction::Assign {
            session_id: session.id.clone(),
            execution_id: session.execution_id.clone(),
            payload: task,
            resume,
            cwd: session.cwd.clone(),
            driver,
            agent_config: effective_agent_config,
            agent_session_id: session.agent_session_id.clone(),
            project_id: tx_exec.project_id,
            mcp_servers,
            next_msg_seq,
        },
    })
}

/// Issue a feed_turn command.
async fn issue_feed_turn(
    pool: &DbPool,
    session: &db::sessions::Session,
) -> Result<ReconcilerAction, SchedulerError> {
    let token = uuid::Uuid::new_v4().to_string();

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let tx_exec = db::executions::get_in_tx(pool, &mut tx, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("execution recheck failed: {e}")))?;
    if tx_exec.desired == "terminate" || tx_exec.outcome.is_some() {
        let _ = tx.rollback().await;
        return Ok(ReconcilerAction::NoAction);
    }

    let select_query = if pool.is_postgres() {
        "SELECT id, execution_id, session_id, task_payload \
         FROM task_queue WHERE session_id = $1 \
         ORDER BY queued_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
    } else {
        "SELECT id, execution_id, session_id, task_payload \
         FROM task_queue WHERE session_id = ? \
         ORDER BY queued_at ASC LIMIT 1"
    };
    let row = sqlx::query(select_query)
        .bind(&session.id)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("select task failed: {e}")))?;

    let Some(row) = row else {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
        return Ok(ReconcilerAction::NoAction);
    };

    let row_id: i64 = row
        .try_get("id")
        .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?;
    let payload_json: String = row
        .try_get("task_payload")
        .map_err(|e| SchedulerError::Database(format!("get task_payload failed: {e}")))?;
    let task_payload: serde_json::Value = serde_json::from_str(&payload_json)
        .map_err(|e| SchedulerError::Database(format!("deserialize task_payload failed: {e}")))?;

    let delete_query = if pool.is_postgres() {
        "DELETE FROM task_queue WHERE id = $1"
    } else {
        "DELETE FROM task_queue WHERE id = ?"
    };
    sqlx::query(delete_query)
        .bind(row_id)
        .execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("delete task failed: {e}")))?;

    let sql = pool.prepare_query(
        "UPDATE sessions SET command_token = ?, command_type = 'feed_turn', \
         command_at = CURRENT_TIMESTAMP, command_has_payload = TRUE, \
         updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND command_token IS NULL AND desired = 'run' AND worker_id = ? \
         AND executor_state IN ('running', 'idle')",
    );
    let result = sqlx::query(&sql)
        .bind(&token)
        .bind(&session.id)
        .bind(&session.worker_id)
        .execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("issue feed_turn failed: {e}")))?;

    if result.rows_affected() == 0 {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
        return Ok(ReconcilerAction::NoAction);
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit failed: {e}")))?;

    Ok(ReconcilerAction::SendCommand {
        token,
        action: CommandAction::FeedTurn {
            session_id: session.id.clone(),
            payload: task_payload,
        },
    })
}

/// Issue a stop_turn command (no payload).
async fn issue_stop_turn(
    pool: &DbPool,
    session: &db::sessions::Session,
) -> Result<ReconcilerAction, SchedulerError> {
    let token = uuid::Uuid::new_v4().to_string();

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin stop_turn tx: {e}")))?;

    let sql = pool.prepare_query(
        "UPDATE sessions SET command_token = ?, command_type = 'stop_turn', \
         command_at = CURRENT_TIMESTAMP, command_has_payload = FALSE, \
         updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND worker_id = ? AND desired = 'stop'",
    );
    let result = sqlx::query(&sql)
        .bind(&token)
        .bind(&session.id)
        .bind(&session.worker_id)
        .execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("issue stop_turn failed: {e}")))?;

    if result.rows_affected() == 0 {
        let _ = tx.rollback().await;
        return Ok(ReconcilerAction::NoAction);
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit stop_turn tx: {e}")))?;

    Ok(ReconcilerAction::SendCommand {
        token,
        action: CommandAction::StopTurn {
            session_id: session.id.clone(),
        },
    })
}

/// Issue a cancel command (no payload).
async fn issue_cancel(
    pool: &DbPool,
    session: &db::sessions::Session,
) -> Result<ReconcilerAction, SchedulerError> {
    let token = uuid::Uuid::new_v4().to_string();

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin cancel tx: {e}")))?;

    let sql = pool.prepare_query(
        "UPDATE sessions SET command_token = ?, command_type = 'cancel', \
         command_at = CURRENT_TIMESTAMP, command_has_payload = FALSE, \
         updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND worker_id = ? AND desired = 'terminate'",
    );
    let result = sqlx::query(&sql)
        .bind(&token)
        .bind(&session.id)
        .bind(&session.worker_id)
        .execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("issue cancel failed: {e}")))?;

    if result.rows_affected() == 0 {
        let _ = tx.rollback().await;
        return Ok(ReconcilerAction::NoAction);
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit cancel tx: {e}")))?;

    Ok(ReconcilerAction::SendCommand {
        token,
        action: CommandAction::Cancel {
            session_id: session.id.clone(),
        },
    })
}

/// Terminate a session and its subtree.
async fn terminate_and_cascade(
    pool: &DbPool,
    session: &db::sessions::Session,
    desired_by: &str,
) -> Result<(), SchedulerError> {
    match transition::terminate_session(pool, &session.execution_id, &session.id, desired_by).await
    {
        Ok(()) => {}
        Err(transition::Rejected::Ratchet) => {}
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "{desired_by} terminate failed: {e:?}"
            )));
        }
    }
    let fresh = db::sessions::get_by_id(pool, &session.id).await?;
    cascade_children(pool, &fresh).await
}

/// Cascade terminate to non-terminal descendants.
pub async fn cascade_children(
    pool: &DbPool,
    session: &db::sessions::Session,
) -> Result<(), SchedulerError> {
    let children = db::sessions::get_children(pool, &session.id).await?;
    for child in &children {
        if child.outcome.is_none() && child.desired != "terminate" {
            let result = transition::transition(
                pool,
                &session.execution_id,
                &child.id,
                transition::Action::SetDesired(
                    transition::Desired::Terminate,
                    "system:cascade".to_string(),
                ),
            )
            .await;
            if let Err(e) = result {
                let settled = matches!(e, transition::Rejected::Ratchet)
                    && db::sessions::get_by_id(pool, &child.id)
                        .await
                        .is_ok_and(|c| c.desired == "terminate");
                if !settled {
                    return Err(SchedulerError::Database(format!(
                        "cascade to {} failed: {e:?}",
                        child.id
                    )));
                }
            }
        }
        let fresh_child = db::sessions::get_by_id(pool, &child.id).await?;
        Box::pin(cascade_children(pool, &fresh_child)).await?;
    }
    Ok(())
}

/// Repair the execution of a root session that is already terminal.
pub async fn repair_execution_for_terminal_root(
    pool: &DbPool,
    root_session_id: &str,
) -> Result<(), SchedulerError> {
    let session = db::sessions::get_by_id(pool, root_session_id).await?;
    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id).await?;
    let repaired = async {
        let tx_root = db::sessions::get_in_tx(pool, &mut tx, root_session_id).await?;
        if tx_root.outcome.is_none() || tx_root.desired != "terminate" {
            return Ok(());
        }
        let tx_exec = db::executions::get_in_tx(pool, &mut tx, &session.execution_id).await?;
        if tx_exec.outcome.is_some() {
            return Ok(());
        }
        let derived =
            transition::derive_execution_outcome(pool, &mut tx, &session.execution_id).await?;
        transition::write_execution_terminal_in_tx(pool, &mut tx, &session.execution_id, &derived)
            .await
            .map(|_| ())
    }
    .await;
    match repaired {
        Ok(()) => tx
            .commit()
            .await
            .map_err(|e| SchedulerError::Database(format!("commit repair tx: {e}"))),
        Err(e) => {
            let _ = tx.rollback().await;
            Err(e)
        }
    }
}

/// Finalize a terminal session.
pub async fn finalize(
    pool: &DbPool,
    session: &db::sessions::Session,
) -> Result<(), SchedulerError> {
    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id).await?;
    let outcome = async {
        let current = match db::sessions::get_in_tx(pool, &mut tx, &session.id).await {
            Ok(current) => current,
            Err(SchedulerError::NotFound(_)) => return Ok(()),
            Err(e) => return Err(e),
        };
        if current.desired != "terminate" {
            tracing::debug!(
                session_id = %current.id,
                desired = %current.desired,
                "finalize skipped: the session is no longer terminating"
            );
            return Ok(());
        }

        db::task_queue::delete_by_session_in_tx(pool, &mut tx, &current.id).await?;

        notify_parent_of_crash_in_tx(pool, &mut tx, &current).await?;

        if current.worker_id.is_some() {
            let sql = pool.prepare_query(
                "UPDATE sessions SET worker_id = NULL, updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ?",
            );
            sqlx::query(&sql)
                .bind(&current.id)
                .execute(&mut **tx)
                .await
                .map_err(|e| SchedulerError::Database(format!("clear worker_id failed: {e}")))?;
        }
        Ok(())
    }
    .await;

    match outcome {
        Ok(()) => tx
            .commit()
            .await
            .map_err(|e| SchedulerError::Database(format!("commit finalize tx: {e}"))),
        Err(e) => {
            let _ = tx.rollback().await;
            Err(e)
        }
    }
}

/// Notify parent session of child crash (idempotent).
async fn notify_parent_of_crash_in_tx(
    pool: &DbPool,
    tx: &mut db::executions::ExecutionTx<'_>,
    session: &db::sessions::Session,
) -> Result<(), SchedulerError> {
    tracing::debug!(
        session_id = %session.id,
        desired_by = ?session.desired_by,
        parent_notified = %session.parent_notified,
        parent_id = ?session.parent_session_id,
        "notify_parent_of_crash called"
    );

    let desired_by = match session.desired_by.as_deref() {
        Some(s) if s.starts_with("system:crash") => s,
        _ => {
            return Ok(());
        }
    };

    let parent_id = match session.parent_session_id.as_deref() {
        Some(id) => id,
        None => return Ok(()),
    };

    if session.parent_notified {
        return Ok(());
    }

    let text = format!("Child session {} crashed.", session.id);
    let notification = serde_json::json!({
        "message": {
            "role": "ROLE_USER",
            "parts": [
                {"text": text},
                {"data": {
                    "type": "child_crashed",
                    "child_session_id": &session.id,
                    "desired_by": desired_by,
                    "outcome": session.outcome.as_deref().unwrap_or("failed"),
                }}
            ]
        }
    });
    let notification_str = serde_json::to_string(&notification).unwrap_or_default();
    let source = format!("child_result:{}", session.id);
    let event_payload = serde_json::json!({
        "type": "child_crashed",
        "child_session_id": &session.id,
    });
    let event_str = serde_json::to_string(&event_payload).unwrap_or_default();

    let tx_exec = db::executions::get_in_tx(pool, &mut *tx, &session.execution_id).await?;
    if tx_exec.desired == "terminate" || tx_exec.outcome.is_some() {
        return Ok(());
    }
    let tx_parent = match db::sessions::get_in_tx(pool, &mut *tx, parent_id).await {
        Ok(parent) => parent,
        Err(SchedulerError::NotFound(_)) => return Ok(()),
        Err(e) => return Err(e),
    };
    if tx_parent.outcome.is_some() || tx_parent.desired == "terminate" {
        return Ok(());
    }

    let notify_sql = pool.prepare_query(
        "UPDATE sessions SET parent_notified = TRUE, updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND parent_notified = FALSE \
         AND desired = 'terminate' AND outcome IS NOT NULL AND desired_by = ?",
    );
    let notify_result = sqlx::query(&notify_sql)
        .bind(&session.id)
        .bind(desired_by)
        .execute(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("CAS parent_notified: {e}")))?;
    if notify_result.rows_affected() == 0 {
        return Ok(());
    }

    if tx_parent.desired == "stop" {
        let resume_sql = pool.prepare_query(
            "UPDATE sessions SET desired = 'run', desired_by = 'system:child_crash_notify', \
             desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND desired = 'stop'",
        );
        let resume_result = sqlx::query(&resume_sql)
            .bind(parent_id)
            .execute(&mut ***tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("auto-resume parent failed: {e}")))?;

        if resume_result.rows_affected() > 0 {
            let resume_event =
                serde_json::json!({"desired": "run", "desired_by": "system:child_crash_notify"});
            let resume_event_str = serde_json::to_string(&resume_event).unwrap_or_default();
            db::events::insert_in_tx(
                pool,
                &mut *tx,
                Some(parent_id),
                "state_change",
                &resume_event_str,
            )
            .await
            .map_err(|e| {
                SchedulerError::Database(format!("auto-resume state_change event: {e}"))
            })?;
        }
    }

    let enqueue_sql = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
    );
    sqlx::query(&enqueue_sql)
        .bind(&session.execution_id)
        .bind(parent_id)
        .bind(&notification_str)
        .bind(&source)
        .execute(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("enqueue parent notification: {e}")))?;

    db::events::insert_in_tx(pool, &mut *tx, Some(parent_id), "platform", &event_str)
        .await
        .map_err(|e| SchedulerError::Database(format!("persist crash notice event: {e}")))?;

    Ok(())
}

/// Build driver info for assign command.
async fn build_driver_info_in_tx(
    agent: &db::agents::Agent,
    session: &db::sessions::Session,
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
) -> Result<serde_json::Value, SchedulerError> {
    let driver = if let Some(ref driver_id) = agent.driver_id {
        db::drivers::get_by_id_in_tx(pool, tx, driver_id).await.ok()
    } else {
        None
    };

    let platform = driver
        .as_ref()
        .map(|d| d.platform.as_str())
        .unwrap_or(&agent.agent_type);

    let config = if agent.agent_type == "claude_sdk"
        || agent.agent_type == "copilot_sdk"
        || agent.agent_type == "codex_sdk"
    {
        let parsed = crate::services::sandbox::parse_sandbox_policy(&session.sandbox_policy)?;
        crate::services::sandbox::build_sandbox_driver_config(&parsed)?
    } else {
        serde_json::from_str(&agent.config).unwrap_or(serde_json::json!({}))
    };

    Ok(serde_json::json!({
        "platform": platform,
        "config": config,
    }))
}
