use serde_json::json;
use tokio::sync::broadcast;

use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::TaskQueue;

/// How to transition sessions during cascade termination.
#[derive(Debug, Clone, Copy)]
pub enum CascadeMode {
    /// Context-dependent: input-required→completed, working/submitted→canceled.
    /// Used by release, session complete.
    Release,
    /// All non-terminal sessions → canceled.
    /// Used by execution cancel, session cancel, crash cascade.
    Cancel,
}

pub struct CascadeResult {
    pub sessions_terminated: usize,
}

/// Terminate a session subtree. Transitions each non-terminal session to an
/// appropriate terminal state, logs state_change events, and wakes workers
/// so they discover the termination.
///
/// When `include_root` is true, the root session is also transitioned.
/// When false, only descendants are affected (caller handles root separately).
pub async fn terminate_subtree(
    pool: &DbPool,
    root_session_id: &str,
    include_root: bool,
    mode: CascadeMode,
    event_broadcast: &broadcast::Sender<EventNotification>,
    task_queue: &TaskQueue,
) -> Result<CascadeResult, SchedulerError> {
    let sessions = db::sessions::get_subtree(pool, root_session_id).await?;
    if sessions.is_empty() {
        tracing::warn!(
            root_session_id,
            "terminate_subtree: root session not found in DB"
        );
        return Ok(CascadeResult {
            sessions_terminated: 0,
        });
    }
    let mut terminated = 0;

    let execution_id = sessions[0].execution_id.clone();

    for session in &sessions {
        if !include_root && session.id == root_session_id {
            continue;
        }

        // Skip already-terminal sessions
        if matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
            continue;
        }

        let target_status = match mode {
            CascadeMode::Cancel => "canceled",
            CascadeMode::Release => match session.status.as_str() {
                "input-required" => "completed",
                _ => "canceled", // working, submitted
            },
        };

        db::sessions::update_status(pool, &session.id, target_status).await?;

        let state_event = json!({"from": session.status, "to": target_status});
        let event_id = db::events::insert(
            pool,
            &execution_id,
            Some(&session.id),
            "state_change",
            &serde_json::to_string(&state_event).unwrap(),
        )
        .await?;
        let _ = event_broadcast.send(EventNotification {
            execution_id: execution_id.clone(),
            event_id,
        });

        terminated += 1;
    }

    // Wake long-polling workers so they discover terminal states immediately
    if terminated > 0 {
        task_queue.wake_waiters();
    }

    Ok(CascadeResult {
        sessions_terminated: terminated,
    })
}
