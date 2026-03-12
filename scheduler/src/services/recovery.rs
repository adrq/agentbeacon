use chrono::{DateTime, Utc};
use serde_json::json;
use tokio::sync::broadcast;

use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::TaskQueue;

pub struct RecoveryStats {
    pub recovered: usize,
    pub failed: usize,
    pub skipped: usize,
}

pub struct LivenessScanStats {
    pub recovered: usize,
    pub failed: usize,
    pub skipped: usize,
    pub non_resumable_failed: usize,
}

/// Scan for orphaned sessions and resubmit them for recovery.
///
/// Runs after a grace period following scheduler startup. Sessions whose
/// `updated_at` is older than `updated_before` (scheduler startup time) are
/// considered orphaned — their worker didn't reconnect during the grace period.
///
/// Infallible — logs errors internally (matches crash.rs pattern).
pub async fn recover_orphaned_sessions(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    max_recovery_attempts: i64,
    updated_before: DateTime<Utc>,
) -> RecoveryStats {
    let mut stats = RecoveryStats {
        recovered: 0,
        failed: 0,
        skipped: 0,
    };

    // Format timestamp for DB comparison, accounting for backend precision:
    // - PostgreSQL TIMESTAMPTZ has microsecond precision → use sub-second format
    //   with explicit +00 timezone so it's not interpreted as server-local time.
    // - SQLite CURRENT_TIMESTAMP has second precision → use seconds-only format.
    //   Sessions updated in the exact startup second may be missed by `<` (equal,
    //   not less than). This is acceptable: the grace period (default 30s) ensures
    //   live workers heartbeat well past startup, and any missed orphan is caught
    //   on the next restart.
    let updated_before_str = if pool.is_postgres() {
        updated_before
            .format("%Y-%m-%d %H:%M:%S%.6f+00")
            .to_string()
    } else {
        updated_before.format("%Y-%m-%d %H:%M:%S").to_string()
    };

    tracing::info!(
        updated_before = %updated_before_str,
        max_recovery_attempts,
        "recovery scan: querying for recoverable sessions"
    );

    // Find recoverable sessions (within budget)
    let sessions = match db::sessions::find_recoverable(
        pool,
        max_recovery_attempts,
        &updated_before_str,
    )
    .await
    {
        Ok(s) => {
            tracing::info!(count = s.len(), "recovery scan: found recoverable sessions");
            s
        }
        Err(e) => {
            tracing::error!(error = %e, "recovery scan: find_recoverable query failed");
            return stats;
        }
    };

    for session in &sessions {
        match recover_session(
            pool,
            task_queue,
            event_broadcast,
            session,
            &updated_before_str,
        )
        .await
        {
            Ok(true) => stats.recovered += 1,
            Ok(false) => {
                tracing::info!(
                    session_id = %session.id,
                    "recovery: session updated since scan, skipped"
                );
                stats.skipped += 1;
            }
            Err(e) => {
                tracing::error!(
                    session_id = %session.id,
                    error = %e,
                    "recovery scan: failed to recover session"
                );
                stats.failed += 1;
            }
        }
    }

    // Permanently fail sessions that have exhausted their recovery budget
    let over_budget = match db::sessions::find_over_budget(
        pool,
        max_recovery_attempts,
        &updated_before_str,
    )
    .await
    {
        Ok(s) => s,
        Err(e) => {
            tracing::error!(error = %e, "recovery scan: find_over_budget query failed");
            return stats;
        }
    };

    for session in &over_budget {
        match db::sessions::try_claim_for_failure(pool, &session.id, &updated_before_str).await {
            Ok(false) => {
                tracing::info!(
                    session_id = %session.id,
                    "recovery: over-budget session updated since scan, skipping"
                );
                stats.skipped += 1;
                continue;
            }
            Err(e) => {
                tracing::error!(
                    session_id = %session.id,
                    error = %e,
                    "recovery: CAS claim failed for over-budget session"
                );
                stats.failed += 1;
                continue;
            }
            Ok(true) => {} // claimed, proceed
        }
        tracing::warn!(
            session_id = %session.id,
            recovery_attempts = session.recovery_attempts,
            max = max_recovery_attempts,
            "recovery budget exhausted, permanently failing session"
        );

        // Emit state_change event here because try_claim_for_failure() already
        // set status='failed', so handle_session_failure() will skip its own
        // transition + event insert. We have the correct prior status and error.
        let error_msg = format!(
            "recovery budget exhausted after {} attempts",
            session.recovery_attempts
        );
        let event_payload = json!({
            "from": session.status,
            "to": "failed",
            "error_kind": "recovery_exhausted",
            "error": &error_msg,
        });
        match db::events::insert(
            pool,
            &session.execution_id,
            Some(&session.id),
            "state_change",
            &serde_json::to_string(&event_payload).unwrap(),
        )
        .await
        {
            Ok(event_id) => {
                let _ = event_broadcast.send(EventNotification::persisted(
                    session.execution_id.clone(),
                    event_id,
                ));
            }
            Err(e) => {
                tracing::warn!(
                    session_id = %session.id,
                    error = %e,
                    "recovery: failed to insert state_change event for over-budget failure"
                );
            }
        }

        crate::services::crash::handle_session_failure(
            pool,
            task_queue,
            event_broadcast,
            &session.id,
            Some("recovery_exhausted"),
            Some(&format!(
                "recovery budget exhausted after {} attempts",
                session.recovery_attempts
            )),
            None,
        )
        .await;
        stats.failed += 1;
    }

    stats
}

/// Run a full liveness scan: recover resumable sessions + fail non-resumable ones.
pub async fn run_liveness_scan(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    max_recovery_attempts: i64,
    updated_before: DateTime<Utc>,
) -> LivenessScanStats {
    // Resumable sessions (claude_sdk, copilot_sdk)
    let recovery = recover_orphaned_sessions(
        pool,
        task_queue,
        event_broadcast,
        max_recovery_attempts,
        updated_before,
    )
    .await;

    // Non-resumable sessions (ACP, deleted agents, etc.)
    let non_resumable_failed =
        fail_stale_non_resumable(pool, task_queue, event_broadcast, updated_before).await;

    LivenessScanStats {
        recovered: recovery.recovered,
        failed: recovery.failed,
        skipped: recovery.skipped,
        non_resumable_failed,
    }
}

/// Permanently fail stale sessions for non-resumable agent types.
/// Infallible — logs errors internally.
async fn fail_stale_non_resumable(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    updated_before: DateTime<Utc>,
) -> usize {
    let updated_before_str = if pool.is_postgres() {
        updated_before
            .format("%Y-%m-%d %H:%M:%S%.6f+00")
            .to_string()
    } else {
        updated_before.format("%Y-%m-%d %H:%M:%S").to_string()
    };

    let sessions = match db::sessions::find_stale_non_resumable(pool, &updated_before_str).await {
        Ok(s) => s,
        Err(e) => {
            tracing::error!(error = %e, "liveness scan: find_stale_non_resumable query failed");
            return 0;
        }
    };

    if sessions.is_empty() {
        return 0;
    }

    tracing::info!(
        count = sessions.len(),
        "liveness scan: found stale non-resumable sessions"
    );

    let mut count = 0usize;
    for session in &sessions {
        match db::sessions::try_claim_for_failure(pool, &session.id, &updated_before_str).await {
            Ok(false) => {
                tracing::info!(
                    session_id = %session.id,
                    "liveness: non-resumable session updated since scan, skipping"
                );
                continue;
            }
            Err(e) => {
                tracing::error!(
                    session_id = %session.id,
                    error = %e,
                    "liveness: CAS claim failed for non-resumable session"
                );
                continue;
            }
            Ok(true) => {} // claimed
        }

        tracing::warn!(
            session_id = %session.id,
            agent_id = %session.agent_id,
            status = %session.status,
            "permanently failing non-resumable stale session"
        );

        // Emit state_change event (try_claim_for_failure already set status='failed',
        // so handle_session_failure will skip its own transition + event)
        let event_payload = json!({
            "from": session.status,
            "to": "failed",
            "error_kind": "non_resumable_stale",
            "error": "session agent type does not support resume",
        });
        match db::events::insert(
            pool,
            &session.execution_id,
            Some(&session.id),
            "state_change",
            &serde_json::to_string(&event_payload).unwrap(),
        )
        .await
        {
            Ok(event_id) => {
                let _ = event_broadcast.send(EventNotification::persisted(
                    session.execution_id.clone(),
                    event_id,
                ));
            }
            Err(e) => {
                tracing::warn!(
                    session_id = %session.id,
                    error = %e,
                    "liveness: failed to insert state_change event for non-resumable failure"
                );
            }
        }

        // Cascade children + notify parent/execution
        crate::services::crash::handle_session_failure(
            pool,
            task_queue,
            event_broadcast,
            &session.id,
            Some("non_resumable_stale"),
            Some("session agent type does not support resume"),
            None,
        )
        .await;

        count += 1;
    }

    count
}

/// Build the task payload for resuming a session (shared by auto and manual recovery).
async fn build_recovery_task_payload(
    pool: &DbPool,
    session: &db::sessions::Session,
    agent: &db::agents::Agent,
    execution: &db::Execution,
    recovery_message: &str,
) -> Result<serde_json::Value, SchedulerError> {
    let cwd = session.cwd.as_deref().ok_or_else(|| {
        SchedulerError::ValidationFailed("no working directory available for recovery".to_string())
    })?;

    let mut agent_config: serde_json::Value =
        serde_json::from_str::<serde_json::Value>(&agent.config)
            .ok()
            .filter(|v| v.is_object())
            .unwrap_or_else(|| json!({}));
    let sandbox_config: serde_json::Value = agent
        .sandbox_config
        .as_ref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_else(|| json!({}));

    let depth = db::sessions::compute_depth(pool, &session.id, &session.execution_id).await?;
    let role = if session.parent_session_id.is_none() {
        crate::services::briefing::BriefingRole::RootLead
    } else if depth >= execution.max_depth {
        crate::services::briefing::BriefingRole::Leaf
    } else {
        crate::services::briefing::BriefingRole::SubLead
    };
    let hier_name =
        crate::services::messaging::hierarchical_name_for_session(pool, &session.id).await?;
    let parent_info = match &session.parent_session_id {
        Some(pid) => crate::services::messaging::hierarchical_name_for_session(pool, pid).await?,
        None => "user".to_string(),
    };
    let slug = if session.slug.is_empty() {
        session.id[..session.id.len().min(8)].to_string()
    } else {
        session.slug.clone()
    };
    let briefing_ctx = crate::services::briefing::BriefingContext {
        role,
        slug,
        hierarchical_name: hier_name,
        agent_config_name: agent.name.clone(),
        parent_info,
    };
    let briefing = crate::services::briefing::build_environment_briefing(pool, &briefing_ctx).await;
    let existing_prompt = agent.system_prompt.as_deref().unwrap_or("");
    agent_config["system_prompt"] = serde_json::Value::String(
        crate::services::briefing::prepend_briefing(&briefing, existing_prompt),
    );

    let mut task_payload = json!({
        "agent_id": agent.id,
        "driver": {
            "platform": agent.agent_type,
            "config": sandbox_config,
        },
        "agent_config": agent_config,
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": recovery_message}]
        },
        "cwd": cwd,
        "resumeSessionId": session.agent_session_id,
    });
    if let Some(ref pid) = execution.project_id {
        task_payload["project_id"] = serde_json::Value::String(pid.clone());

        // Inject project MCP servers into recovery payload (same as root/delegate)
        let mcp_servers = db::project_mcp_servers::list_by_project(pool, pid).await?;
        if !mcp_servers.is_empty() {
            task_payload["mcp_servers"] =
                crate::services::mcp::build_mcp_servers_payload(&mcp_servers);
        }
    }

    Ok(task_payload)
}

/// Recover a single orphaned session: atomically claim via CAS, reset to
/// submitted, and enqueue a resume task — all within a single transaction.
///
/// Returns `Ok(true)` if recovered, `Ok(false)` if the session was updated
/// between the SELECT scan and the CAS (worker reconnected — skip).
async fn recover_session(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session: &db::sessions::Session,
    updated_before: &str,
) -> Result<bool, SchedulerError> {
    // --- Pre-transaction reads ---

    // Look up agent — NotFound means deleted, permanently fail.
    // Transient DB errors are propagated so the session can be retried next restart.
    let agent = match db::agents::get_by_id(pool, &session.agent_id).await {
        Ok(a) => a,
        Err(SchedulerError::NotFound(_)) => {
            tracing::warn!(
                session_id = %session.id,
                agent_id = %session.agent_id,
                "recovery: agent not found or deleted, permanently failing session"
            );
            crate::services::crash::handle_session_failure(
                pool,
                task_queue,
                event_broadcast,
                &session.id,
                Some("recovery_failed"),
                Some("agent not found or deleted during recovery"),
                None,
            )
            .await;
            return Ok(true);
        }
        Err(e) => return Err(e),
    };

    // Guard: cwd must be present (required for resume)
    if session.cwd.is_none() {
        tracing::warn!(
            session_id = %session.id,
            "recovery: session has no cwd, permanently failing"
        );
        crate::services::crash::handle_session_failure(
            pool,
            task_queue,
            event_broadcast,
            &session.id,
            Some("recovery_failed"),
            Some("session has no cwd — cannot resume"),
            None,
        )
        .await;
        return Ok(true);
    }

    let prior_status = session.status.clone();

    // --- Briefing build + task payload construction (outside transaction) ---

    let recovery_message = "[recovery] Session resumed.";

    let execution = db::executions::get_by_id(pool, &session.execution_id).await?;

    let task_payload =
        build_recovery_task_payload(pool, session, &agent, &execution, recovery_message).await?;

    // --- Atomic CAS UPDATE + task_queue INSERT in a single transaction ---

    let ts_cast = if pool.is_postgres() {
        "::timestamptz"
    } else {
        ""
    };
    let cas_sql = format!(
        "UPDATE sessions SET recovery_attempts = recovery_attempts + 1, \
         status = 'submitted', updated_at = CURRENT_TIMESTAMP, completed_at = NULL \
         WHERE id = ? AND status IN ('working', 'input-required') \
         AND updated_at < ?{ts_cast} \
         AND agent_session_id IS NOT NULL AND cwd IS NOT NULL"
    );
    let cas_query = pool.prepare_query(&cas_sql);

    let insert_sql = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
    );
    let payload_json = serde_json::to_string(&task_payload)
        .map_err(|e| SchedulerError::Database(format!("serialize task_payload failed: {e}")))?;

    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let cas_result = sqlx::query(&cas_query)
        .bind(&session.id)
        .bind(updated_before)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("CAS recovery claim failed: {e}")))?;

    if cas_result.rows_affected() == 0 {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
        return Ok(false);
    }

    sqlx::query(&insert_sql)
        .bind(&session.execution_id)
        .bind(&session.id)
        .bind(&payload_json)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recovery task_queue insert failed: {e}")))?;

    tx.commit().await.map_err(|e| {
        SchedulerError::Database(format!("commit recovery transaction failed: {e}"))
    })?;

    task_queue.wake_waiters();

    // --- Post-transaction best-effort observability ---

    // If root lead was input-required, also reset execution status to submitted
    // to match the session recovery. Don't regress working→submitted when
    // children may still be active on surviving workers.
    if session.parent_session_id.is_none() && prior_status == "input-required" {
        use db::executions::CasResult;
        match db::executions::update_status_cas(
            pool,
            &session.execution_id,
            "submitted",
            &["input-required"],
        )
        .await
        {
            Ok(CasResult::Applied) => {}
            Ok(CasResult::Conflict) => {
                tracing::warn!(
                    execution_id = %session.execution_id,
                    "recovery: execution no longer input-required — skipping reset"
                );
            }
            Ok(CasResult::NotFound) => {
                tracing::error!(
                    execution_id = %session.execution_id,
                    "recovery: execution row missing — data integrity issue"
                );
            }
            Err(e) => {
                tracing::warn!(
                    execution_id = %session.execution_id,
                    error = %e,
                    "recovery: failed to reset execution status"
                );
            }
        }
    }

    // Log state_change event
    let event_payload = json!({
        "from": prior_status,
        "to": "submitted",
        "recovery_attempt": session.recovery_attempts + 1,
    });
    match db::events::insert(
        pool,
        &session.execution_id,
        Some(&session.id),
        "state_change",
        &serde_json::to_string(&event_payload).unwrap(),
    )
    .await
    {
        Ok(event_id) => {
            let _ = event_broadcast.send(EventNotification::persisted(
                session.execution_id.clone(),
                event_id,
            ));
        }
        Err(e) => {
            tracing::warn!(
                session_id = %session.id,
                error = %e,
                "recovery: failed to insert state_change event"
            );
        }
    }

    tracing::info!(
        session_id = %session.id,
        execution_id = %session.execution_id,
        prior_status = %prior_status,
        recovery_attempt = session.recovery_attempts + 1,
        "session recovered and resubmitted"
    );

    Ok(true)
}

// --- Manual (user-initiated) recovery ---

pub struct ManualRecoveryResult {
    pub session_id: String,
    pub execution_id: String,
    pub execution_recovered: bool,
}

/// Resumable agent types that support recovery (have persistent session state).
fn is_resumable_agent_type(agent_type: &str) -> bool {
    matches!(agent_type, "claude_sdk" | "copilot_sdk")
}

/// Attempt manual recovery of a failed session.
///
/// Resets the session to `submitted`, resets `recovery_attempts` to 0,
/// enqueues a resume task, and optionally recovers the execution if
/// this is the root lead session of a failed execution.
pub async fn attempt_manual_recovery(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session_id: &str,
    user_message: Option<&str>,
) -> Result<ManualRecoveryResult, SchedulerError> {
    // 1. Get session, verify status == "failed"
    let session = db::sessions::get_by_id(pool, session_id).await?;
    if session.status != "failed" {
        return Err(SchedulerError::Conflict(format!(
            "session must be 'failed' to recover (current: {})",
            session.status
        )));
    }

    // 2. Get execution
    let execution = db::executions::get_by_id(pool, &session.execution_id).await?;

    // 3. Execution state guard
    let is_root_lead = session.parent_session_id.is_none();
    let mut recover_execution = false;
    match execution.status.as_str() {
        "completed" => {
            return Err(SchedulerError::Conflict(
                "cannot recover session in a completed execution".to_string(),
            ));
        }
        "canceled" => {
            return Err(SchedulerError::Conflict(
                "cannot recover session in a canceled execution".to_string(),
            ));
        }
        "failed" => {
            if is_root_lead {
                recover_execution = true;
            } else {
                return Err(SchedulerError::Conflict(
                    "cannot recover child session while execution is failed — recover the root lead session first".to_string(),
                ));
            }
        }
        _ => {
            // submitted, working, input-required → session-only recovery
        }
    }

    // 4. Validate agent (get_by_id filters deleted_at IS NULL, so NotFound covers deletion;
    //    the explicit deleted_at check is a defensive safety net)
    let agent = db::agents::get_by_id(pool, &session.agent_id).await?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent is disabled: {}",
            agent.name
        )));
    }
    if !is_resumable_agent_type(&agent.agent_type) {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent type '{}' does not support recovery",
            agent.agent_type
        )));
    }

    // 5. Validate session has agent_session_id and cwd
    if session.agent_session_id.is_none() {
        return Err(SchedulerError::ValidationFailed(
            "no agent session ID available for recovery".to_string(),
        ));
    }
    if session.cwd.is_none() {
        return Err(SchedulerError::ValidationFailed(
            "no working directory available for recovery".to_string(),
        ));
    }

    // 6. Build task payload
    let recovery_message = user_message.unwrap_or("[recovery] Session resumed by user.");
    let task_payload =
        build_recovery_task_payload(pool, &session, &agent, &execution, recovery_message).await?;

    // 7. Single atomic transaction
    let session_cas_sql = pool.prepare_query(
        "UPDATE sessions SET status = 'submitted', recovery_attempts = 0, \
         updated_at = CURRENT_TIMESTAMP, completed_at = NULL \
         WHERE id = ? AND status = 'failed'",
    );
    let insert_sql = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
    );
    let payload_json = serde_json::to_string(&task_payload)
        .map_err(|e| SchedulerError::Database(format!("serialize task_payload failed: {e}")))?;

    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    // Session CAS: failed → submitted
    let cas_result = sqlx::query(&session_cas_sql)
        .bind(session_id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("CAS recovery claim failed: {e}")))?;

    if cas_result.rows_affected() == 0 {
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
        return Err(SchedulerError::Conflict(
            "session status changed concurrently — recovery aborted".to_string(),
        ));
    }

    // Re-check execution state inside the transaction for child-only recovery.
    // Guards against TOCTOU: a concurrent cancel/complete could have flipped the
    // execution to terminal after our pre-transaction check but before the CAS.
    if !recover_execution {
        let exec_check_sql = pool.prepare_query("SELECT status FROM executions WHERE id = ?");
        let exec_row = sqlx::query_scalar::<_, String>(&exec_check_sql)
            .bind(&session.execution_id)
            .fetch_one(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("execution re-check failed: {e}")))?;
        if matches!(exec_row.as_str(), "completed" | "canceled" | "failed") {
            tx.rollback()
                .await
                .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
            return Err(SchedulerError::Conflict(format!(
                "execution became '{}' concurrently — recovery aborted",
                exec_row
            )));
        }
    }

    // Drain stale task_queue rows for this session before inserting recovery task.
    // Messages delivered while the session was working (pre-crash) may still be queued.
    // pop_by_session() is FIFO, so without this drain the worker would consume a stale
    // message (lacking resumeSessionId/agent_config) instead of the recovery payload.
    // The messages are preserved in the events table — only the delivery mechanism is cleared.
    let drain_sql = pool.prepare_query("DELETE FROM task_queue WHERE session_id = ?");
    sqlx::query(&drain_sql)
        .bind(session_id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("drain stale tasks failed: {e}")))?;

    // Task queue insert
    sqlx::query(&insert_sql)
        .bind(&session.execution_id)
        .bind(session_id)
        .bind(&payload_json)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recovery task_queue insert failed: {e}")))?;

    // Conditional execution CAS: failed → submitted (only for root lead)
    if recover_execution {
        let exec_cas_sql = pool.prepare_query(
            "UPDATE executions SET status = 'submitted', \
             updated_at = CURRENT_TIMESTAMP, completed_at = NULL \
             WHERE id = ? AND status = 'failed'",
        );
        let exec_result = sqlx::query(&exec_cas_sql)
            .bind(&session.execution_id)
            .execute(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("execution CAS recovery failed: {e}")))?;

        if exec_result.rows_affected() == 0 {
            tx.rollback()
                .await
                .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
            return Err(SchedulerError::Conflict(
                "execution status changed concurrently — recovery aborted".to_string(),
            ));
        }
    }

    tx.commit().await.map_err(|e| {
        SchedulerError::Database(format!("commit manual recovery transaction failed: {e}"))
    })?;

    // 8. Emit state_change events (before waking workers to avoid event ordering race)
    let session_event = json!({
        "from": "failed",
        "to": "submitted",
        "manual_recovery": true,
    });
    match db::events::insert(
        pool,
        &session.execution_id,
        Some(session_id),
        "state_change",
        &serde_json::to_string(&session_event).unwrap(),
    )
    .await
    {
        Ok(event_id) => {
            let _ = event_broadcast.send(EventNotification::persisted(
                session.execution_id.clone(),
                event_id,
            ));
        }
        Err(e) => {
            tracing::warn!(
                session_id = session_id,
                error = %e,
                "manual recovery: failed to insert session state_change event"
            );
        }
    }

    if recover_execution {
        let exec_event = json!({
            "from": "failed",
            "to": "submitted",
            "manual_recovery": true,
        });
        match db::events::insert(
            pool,
            &session.execution_id,
            None,
            "state_change",
            &serde_json::to_string(&exec_event).unwrap(),
        )
        .await
        {
            Ok(event_id) => {
                let _ = event_broadcast.send(EventNotification::persisted(
                    session.execution_id.clone(),
                    event_id,
                ));
            }
            Err(e) => {
                tracing::warn!(
                    execution_id = %session.execution_id,
                    error = %e,
                    "manual recovery: failed to insert execution state_change event"
                );
            }
        }
    }

    // 9. Wake task queue waiters (after events to reduce ordering race window)
    task_queue.wake_waiters();

    tracing::info!(
        session_id = session_id,
        execution_id = %session.execution_id,
        execution_recovered = recover_execution,
        "manual recovery: session resubmitted"
    );

    Ok(ManualRecoveryResult {
        session_id: session_id.to_string(),
        execution_id: session.execution_id.clone(),
        execution_recovered: recover_execution,
    })
}
