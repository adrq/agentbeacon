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
    let cwd = match &session.cwd {
        Some(c) => c.clone(),
        None => {
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
    };

    let prior_status = session.status.clone();

    // --- Briefing build + task payload construction (outside transaction) ---

    let recovery_message = "[recovery] Session resumed.";

    let execution = db::executions::get_by_id(pool, &session.execution_id).await?;

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
    let available_agents = if matches!(
        role,
        crate::services::briefing::BriefingRole::SubLead
            | crate::services::briefing::BriefingRole::RootLead
    ) {
        db::execution_agents::list_agent_configs_for_execution(pool, &session.execution_id).await?
    } else {
        vec![]
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
        available_agents,
    };
    let briefing = crate::services::briefing::build_environment_briefing(&briefing_ctx);
    let existing_prompt = agent_config
        .get("system_prompt")
        .and_then(|v| v.as_str())
        .unwrap_or("");
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
    }

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
