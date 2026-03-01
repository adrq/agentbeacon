use chrono::{DateTime, Utc};
use serde_json::json;
use tokio::sync::broadcast;

use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::queue::{TaskAssignment, TaskQueue};

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
        if let Err(e) = recover_session(pool, task_queue, event_broadcast, session).await {
            tracing::error!(
                session_id = %session.id,
                error = %e,
                "recovery scan: failed to recover session"
            );
            stats.failed += 1;
        } else {
            stats.recovered += 1;
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
        tracing::warn!(
            session_id = %session.id,
            recovery_attempts = session.recovery_attempts,
            max = max_recovery_attempts,
            "recovery budget exhausted, permanently failing session"
        );
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

/// Recover a single orphaned session: increment counter, reset to submitted,
/// and enqueue a resume task.
async fn recover_session(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session: &db::sessions::Session,
) -> Result<(), crate::error::SchedulerError> {
    // Look up agent — NotFound means deleted, permanently fail.
    // Transient DB errors are propagated so the session can be retried next restart.
    let agent = match db::agents::get_by_id(pool, &session.agent_id).await {
        Ok(a) => a,
        Err(crate::error::SchedulerError::NotFound(_)) => {
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
            return Ok(());
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
            return Ok(());
        }
    };

    let prior_status = session.status.clone();

    // Increment recovery counter
    db::sessions::increment_recovery_attempts(pool, &session.id).await?;

    // Reset session to submitted
    db::sessions::update_status(pool, &session.id, "submitted").await?;

    // If root lead was input-required, also reset execution status to submitted
    // to match the session recovery. Don't regress working→submitted when
    // children may still be active on surviving workers.
    if session.parent_session_id.is_none()
        && prior_status == "input-required"
        && let Err(e) =
            db::executions::update_status(pool, &session.execution_id, "submitted").await
    {
        tracing::warn!(
            execution_id = %session.execution_id,
            error = %e,
            "recovery: failed to reset execution status"
        );
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
            let _ = event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id,
            });
        }
        Err(e) => {
            tracing::warn!(
                session_id = %session.id,
                error = %e,
                "recovery: failed to insert state_change event"
            );
        }
    }

    let recovery_message = "[recovery] Session resumed.";

    // Build resume task_payload (superset of initial task_payload)
    let agent_config: serde_json::Value =
        serde_json::from_str(&agent.config).unwrap_or_else(|_| json!({}));
    let sandbox_config: serde_json::Value = agent
        .sandbox_config
        .as_ref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_else(|| json!({}));

    let task_payload = json!({
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

    task_queue
        .push(TaskAssignment {
            execution_id: session.execution_id.clone(),
            session_id: session.id.clone(),
            task_payload,
        })
        .await?;

    tracing::info!(
        session_id = %session.id,
        execution_id = %session.execution_id,
        prior_status = %prior_status,
        recovery_attempt = session.recovery_attempts + 1,
        "session recovered and resubmitted"
    );

    Ok(())
}
