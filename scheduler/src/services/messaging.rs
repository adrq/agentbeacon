use std::collections::HashMap;

use serde_json::json;
use tokio::sync::broadcast;

use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::db::sessions::Session;
use crate::error::SchedulerError;
use crate::queue::{TaskAssignment, TaskQueue};

/// Check if parts contain at least one deliverable content item:
/// a non-empty text part OR a file part with bytes.
pub fn has_deliverable_content(parts: &[serde_json::Value]) -> bool {
    parts.iter().any(|p| {
        let kind = p.get("kind").and_then(|k| k.as_str());
        match kind {
            Some("text") => p
                .get("text")
                .and_then(|t| t.as_str())
                .is_some_and(|t| !t.trim().is_empty()),
            Some("file") => p
                .get("file")
                .and_then(|f| f.get("bytes"))
                .and_then(|b| b.as_str())
                .is_some_and(|b| !b.is_empty()),
            _ => false,
        }
    })
}

pub struct MessageDeliveryResult {
    pub event_id: i64,
    pub session_status: String,
    pub execution_status: String,
}

/// Sender metadata for lateral messages. None = user message.
pub struct SenderInfo {
    pub name: String,
    pub session_id: String,
}

/// Transition a session from input-required → working.
/// Records state_change events, propagates to execution if root session.
/// No-op if session is not in input-required state.
///
/// Shared by: deliver_message(), deliver_to_parent().
/// Safe for deliver_to_parent because the parent's worker stays in long-poll
/// (tokio::select on task_queue.notified()) and will wake on push + notify_waiters.
pub async fn transition_to_working(
    db_pool: &DbPool,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session: &Session,
) -> Result<(String, String), SchedulerError> {
    if session.status != "input-required" {
        let execution = db::executions::get_by_id(db_pool, &session.execution_id).await?;
        return Ok((session.status.clone(), execution.status));
    }

    db::sessions::update_status(db_pool, &session.id, "working").await?;

    let session_state_event = json!({"from": "input-required", "to": "working"});
    let sc_event_id = db::events::insert(
        db_pool,
        &session.execution_id,
        Some(&session.id),
        "state_change",
        &serde_json::to_string(&session_state_event).unwrap(),
    )
    .await?;
    let _ = event_broadcast.send(EventNotification::persisted(
        session.execution_id.clone(),
        sc_event_id,
    ));

    // Propagate to execution if root session.
    // CAS prevents resurrecting a terminal execution (cancel/crash already won).
    let execution_status = if session.parent_session_id.is_none() {
        let execution = db::executions::get_by_id(db_pool, &session.execution_id).await?;
        if execution.status != "working" {
            use db::executions::CasResult;
            match db::executions::update_status_cas(
                db_pool,
                &session.execution_id,
                "working",
                &["submitted", "input-required"],
            )
            .await?
            {
                CasResult::Applied => {
                    let exec_state_event = json!({"from": execution.status, "to": "working"});
                    let exec_event_id = db::events::insert(
                        db_pool,
                        &session.execution_id,
                        None,
                        "state_change",
                        &serde_json::to_string(&exec_state_event).unwrap(),
                    )
                    .await?;
                    let _ = event_broadcast.send(EventNotification::persisted(
                        session.execution_id.clone(),
                        exec_event_id,
                    ));
                }
                CasResult::Conflict => {
                    // Execution is terminal or already working — return actual status
                    return Ok(("working".to_string(), execution.status));
                }
                CasResult::NotFound => {
                    return Err(SchedulerError::NotFound(format!(
                        "execution not found: {}",
                        session.execution_id
                    )));
                }
            }
        }
        "working".to_string()
    } else {
        let execution = db::executions::get_by_id(db_pool, &session.execution_id).await?;
        execution.status
    };

    Ok(("working".to_string(), execution_status))
}

/// Core message delivery: guard, record event, push to inbox, transition state.
/// Used by POST /api/sessions/{id}/message (user) and POST /api/messages (agent).
///
/// Status guard (per D17 "identical state machine"):
/// - input-required: accept, transition to working
/// - working: accept, queue for delivery (no transition)
/// - submitted: reject (agent not started, cannot process messages)
/// - completed/failed/canceled: reject (terminal)
pub async fn deliver_message(
    db_pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    session: &Session,
    parts: &[serde_json::Value],
    sender: Option<&SenderInfo>,
) -> Result<MessageDeliveryResult, SchedulerError> {
    // Status guard — identical for user and agent callers (D17)
    if session.status != "input-required" && session.status != "working" {
        return Err(SchedulerError::Conflict(format!(
            "session cannot accept messages (current status: {})",
            session.status
        )));
    }

    // Build event payload: strict A2A message for persistence/queries.
    // Sender metadata is encoded as an A2A `data` part (not a top-level field).
    let mut event_parts: Vec<serde_json::Value> = parts.to_vec();
    if let Some(s) = sender {
        event_parts.push(json!({"kind": "data", "data": {
            "type": "sender",
            "name": s.name,
            "session_id": s.session_id,
        }}));
    }
    let msg_payload = json!({
        "role": "user",
        "parts": event_parts
    });

    // Record message event on recipient session
    let event_id = db::events::insert(
        db_pool,
        &session.execution_id,
        Some(&session.id),
        "message",
        &serde_json::to_string(&msg_payload).unwrap(),
    )
    .await?;
    let _ = event_broadcast.send(EventNotification::persisted(
        session.execution_id.clone(),
        event_id,
    ));

    // Build delivery payload: prepend sender header to first text part.
    // If no text parts exist (image-only message), add a new text part for the header.
    // File parts pass through unchanged.
    let delivery_parts = if let Some(s) = sender {
        let header = format!(
            "[message from {} \u{00b7} session {}]\n\n",
            s.name, s.session_id
        );
        let mut dp: Vec<serde_json::Value> = parts.to_vec();
        if let Some(pos) = dp
            .iter()
            .position(|p| p.get("kind").and_then(|k| k.as_str()) == Some("text"))
        {
            let existing = dp[pos]["text"].as_str().unwrap_or("");
            dp[pos] = json!({"kind": "text", "text": format!("{header}{existing}")});
        } else {
            dp.insert(0, json!({"kind": "text", "text": header.trim_end()}));
        }
        dp
    } else {
        parts.to_vec()
    };
    let delivery_payload = json!({
        "message": {
            "role": "user",
            "parts": delivery_parts
        }
    });
    // Transition state via shared helper BEFORE push.
    // Must happen first: push() calls notify_waiters(), which wakes the worker.
    // If we push first, the worker wakes, sees input-required, and may go back
    // to sleep before we transition to working.
    let (session_status, execution_status) =
        transition_to_working(db_pool, event_broadcast, session).await?;

    task_queue
        .push(TaskAssignment {
            execution_id: session.execution_id.clone(),
            session_id: session.id.clone(),
            task_payload: delivery_payload,
        })
        .await?;

    Ok(MessageDeliveryResult {
        event_id,
        session_status,
        execution_status,
    })
}

/// Compute hierarchical name for each session.
/// Returns Vec<(session_id, hierarchical_name)>.
///
/// Path = slugs joined by `/` from root to session.
pub async fn compute_hierarchical_names(
    db_pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<(String, String)>, SchedulerError> {
    let sessions = db::sessions::list_by_execution(db_pool, execution_id).await?;

    // Build parent map: session_id → (parent_session_id, slug)
    // Fallback for pre-migration sessions with empty slug: use truncated session ID
    let session_map: HashMap<String, (Option<String>, String)> = sessions
        .iter()
        .map(|s| {
            let slug = if s.slug.is_empty() {
                s.id[..s.id.len().min(8)].to_string()
            } else {
                s.slug.clone()
            };
            (s.id.clone(), (s.parent_session_id.clone(), slug))
        })
        .collect();

    let max_depth = sessions.len(); // cycle guard: path can never exceed session count
    let mut result = Vec::new();
    for s in &sessions {
        // Walk from session to root collecting slugs
        let mut path_parts = Vec::new();
        let mut current_id = s.id.clone();
        for _ in 0..=max_depth {
            let Some((parent, slug)) = session_map.get(&current_id) else {
                break; // parent outside execution (data integrity issue) — stop walk
            };
            path_parts.push(slug.clone());
            match parent {
                Some(pid) => current_id = pid.clone(),
                None => break,
            }
        }
        path_parts.reverse();

        result.push((s.id.clone(), path_parts.join("/")));
    }

    Ok(result)
}

/// Compute hierarchical name for a single session by walking its ancestor chain.
/// O(depth) — only fetches ancestors, not all sessions.
pub async fn hierarchical_name_for_session(
    db_pool: &DbPool,
    session_id: &str,
) -> Result<String, SchedulerError> {
    let mut path_parts = Vec::new();
    let mut current_id = session_id.to_string();

    // Depth guard: max 20 hops (well beyond any practical hierarchy)
    for _ in 0..20 {
        let session = db::sessions::get_by_id(db_pool, &current_id).await?;
        let slug = if session.slug.is_empty() {
            session.id[..session.id.len().min(8)].to_string()
        } else {
            session.slug.clone()
        };
        path_parts.push(slug);
        match session.parent_session_id {
            Some(pid) => current_id = pid,
            None => break,
        }
    }
    path_parts.reverse();
    Ok(path_parts.join("/"))
}

/// Resolve recipient + get sender name in one compute_hierarchical_names call.
pub async fn resolve_recipient_and_sender(
    db_pool: &DbPool,
    execution_id: &str,
    recipient_name: &str,
    sender_session_id: &str,
) -> Result<(Session, String), SchedulerError> {
    let names = compute_hierarchical_names(db_pool, execution_id).await?;

    let sender_name = names
        .iter()
        .find(|(sid, _)| sid == sender_session_id)
        .map(|(_, name)| name.clone())
        .ok_or_else(|| {
            SchedulerError::NotFound(format!(
                "session not found in execution: {sender_session_id}"
            ))
        })?;

    let matches: Vec<_> = names
        .iter()
        .filter(|(_, name)| name == recipient_name)
        .collect();

    let recipient = match matches.len() {
        0 => {
            return Err(SchedulerError::NotFound(format!(
                "no agent found with name: {recipient_name}"
            )));
        }
        1 => db::sessions::get_by_id(db_pool, &matches[0].0).await?,
        _ => {
            return Err(SchedulerError::Conflict(format!(
                "ambiguous recipient: {} sessions match name '{}'",
                matches.len(),
                recipient_name
            )));
        }
    };

    Ok((recipient, sender_name))
}

/// Compute hierarchical name for a single session.
pub async fn sender_hierarchical_name(
    db_pool: &DbPool,
    execution_id: &str,
    session_id: &str,
) -> Result<String, SchedulerError> {
    let names = compute_hierarchical_names(db_pool, execution_id).await?;
    names
        .into_iter()
        .find(|(sid, _)| sid == session_id)
        .map(|(_, name)| name)
        .ok_or_else(|| {
            SchedulerError::NotFound(format!("session not found in execution: {session_id}"))
        })
}
