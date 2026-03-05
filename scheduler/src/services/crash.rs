use serde_json::json;
use tokio::sync::broadcast;

use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::queue::{TaskAssignment, TaskQueue};
use crate::services::cascade::{CascadeMode, terminate_subtree};

/// Handle a session failure: mark failed, cascade children, notify parent,
/// propagate to execution. Infallible — logs errors internally so the
/// worker sync caller can always continue.
pub async fn handle_session_failure(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session_id: &str,
    error_kind: Option<&str>,
    error: Option<&str>,
    stderr: Option<&str>,
) {
    // 1. Fetch session — bail if not found
    let session = match db::sessions::get_by_id(pool, session_id).await {
        Ok(s) => s,
        Err(e) => {
            tracing::error!(session_id, error = %e, "crash handler: session not found");
            return;
        }
    };

    // 2. Skip non-crash terminal states entirely
    if matches!(session.status.as_str(), "completed" | "canceled") {
        tracing::debug!(session_id, status = %session.status, "crash handler: session already terminal, skipping");
        return;
    }

    // 3-4. Transition + event insert — skip if already failed (partial-failure
    //       recovery: downstream actions still need to run)
    if session.status != "failed" {
        if let Err(e) = db::sessions::update_status(pool, session_id, "failed").await {
            tracing::error!(session_id, error = %e, "crash handler: failed to transition session to failed");
            return; // Don't cascade/notify if the session itself isn't terminal yet
        }

        let mut event_payload = json!({"from": session.status, "to": "failed"});
        if let Some(e) = error {
            event_payload["error"] = json!(e);
        }
        if let Some(s) = stderr {
            event_payload["stderr"] = json!(s);
        }
        match db::events::insert(
            pool,
            &session.execution_id,
            Some(session_id),
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
                tracing::error!(session_id, error = %e, "crash handler: failed to insert state_change event");
            }
        }
    }

    // 5. Cascade to children — cancel all descendants
    match terminate_subtree(
        pool,
        session_id,
        false, // don't include root — already transitioned to failed above
        CascadeMode::Cancel,
        event_broadcast,
        task_queue,
    )
    .await
    {
        Ok(result) => {
            if result.sessions_terminated > 0 {
                tracing::info!(
                    session_id,
                    terminated = result.sessions_terminated,
                    "crash cascade terminated child sessions"
                );
            }
        }
        Err(e) => {
            tracing::error!(session_id, error = %e, "crash handler: cascade failed");
        }
    }

    // 6. Notify parent (skip for root leads)
    if session.parent_session_id.is_some() {
        notify_parent_of_crash(
            pool,
            task_queue,
            event_broadcast,
            &session,
            error_kind,
            error,
            stderr,
        )
        .await;
    }

    // 7. Root lead: propagate to execution
    if session.parent_session_id.is_none() {
        propagate_failure_to_execution(pool, event_broadcast, &session).await;
    }
}

/// Push failure notification to parent's inbox. No-op for root leads.
async fn notify_parent_of_crash(
    pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    child_session: &db::sessions::Session,
    error_kind: Option<&str>,
    error: Option<&str>,
    stderr: Option<&str>,
) {
    let parent_id = match &child_session.parent_session_id {
        Some(pid) => pid.clone(),
        None => return,
    };

    // Look up agent name for context prefix
    let agent_name = match db::agents::get_by_id(pool, &child_session.agent_id).await {
        Ok(agent) => agent.name,
        Err(_) => child_session.agent_id.clone(),
    };
    let agent_name = agent_name.replace(['\r', '\n'], " ");
    let agent_name = agent_name.trim();
    let agent_name = if agent_name.is_empty() {
        &child_session.agent_id
    } else {
        agent_name
    };

    // Record platform event on parent (audit trail).
    // Use "child_crashed" for executor_failed/unknown, "child_failed" for
    // policy limits (budget_exceeded, max_turns) to avoid mislabeling.
    let is_crash = matches!(error_kind, Some("executor_failed") | None);
    let event_type = if is_crash {
        "child_crashed"
    } else {
        "child_failed"
    };
    let mut crash_data = json!({
        "type": event_type,
        "child_session_id": child_session.id,
        "agent_name": agent_name,
    });
    if let Some(ek) = error_kind {
        crash_data["error_kind"] = json!(ek);
    }
    if let Some(e) = error {
        crash_data["error"] = json!(e);
    }
    if let Some(s) = stderr {
        crash_data["stderr"] = json!(s);
    }
    let parent_event = json!({
        "role": "agent",
        "parts": [{"kind": "data", "data": crash_data}]
    });
    match db::events::insert(
        pool,
        &child_session.execution_id,
        Some(&parent_id),
        "platform",
        &serde_json::to_string(&parent_event).unwrap(),
    )
    .await
    {
        Ok(event_id) => {
            let _ = event_broadcast.send(EventNotification::persisted(
                child_session.execution_id.clone(),
                event_id,
            ));
        }
        Err(e) => {
            tracing::error!(
                child_session_id = %child_session.id,
                parent_id = %parent_id,
                error = %e,
                "crash handler: failed to insert child_crashed platform event"
            );
        }
    }

    // Push formatted message to parent's task_queue
    let failure_label = if is_crash {
        "crashed".to_string()
    } else {
        match error_kind {
            Some(ek) => format!("failed ({ek})"),
            None => "failed".to_string(),
        }
    };
    let mut formatted_text = format!(
        "[session {} ({}) {}]\n\nError: {}",
        child_session.id,
        agent_name,
        failure_label,
        error.unwrap_or("unknown error"),
    );
    if let Some(stderr_text) = stderr {
        formatted_text.push_str(&format!("\n\nStderr:\n{stderr_text}"));
    }

    let notification = json!({
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": formatted_text}]
        },
    });
    if let Err(e) = task_queue
        .push(TaskAssignment {
            execution_id: child_session.execution_id.clone(),
            session_id: parent_id,
            task_payload: notification,
        })
        .await
    {
        tracing::error!(
            child_session_id = %child_session.id,
            error = %e,
            "crash handler: failed to push crash notification to parent"
        );
    }
}

/// Transition execution to failed when its root lead crashes.
async fn propagate_failure_to_execution(
    pool: &DbPool,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session: &db::sessions::Session,
) {
    let exec_from = match db::executions::get_by_id(pool, &session.execution_id).await {
        Ok(e) => e.status,
        Err(e) => {
            tracing::error!(
                execution_id = %session.execution_id,
                error = %e,
                "crash handler: execution not found"
            );
            return;
        }
    };

    // Skip if execution is already terminal
    if matches!(exec_from.as_str(), "completed" | "failed" | "canceled") {
        tracing::debug!(
            execution_id = %session.execution_id,
            status = %exec_from,
            "crash handler: execution already terminal, skipping"
        );
        return;
    }

    if let Err(e) = db::executions::update_status(pool, &session.execution_id, "failed").await {
        tracing::error!(
            execution_id = %session.execution_id,
            error = %e,
            "crash handler: failed to transition execution to failed"
        );
        return;
    }

    let exec_event = json!({"from": exec_from, "to": "failed"});
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
            tracing::error!(
                execution_id = %session.execution_id,
                error = %e,
                "crash handler: failed to insert execution state_change event"
            );
        }
    }
}
