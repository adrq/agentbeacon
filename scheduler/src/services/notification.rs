use serde_json::json;
use tokio::sync::broadcast;

use crate::api::handlers::worker_sync::TurnMessagePayload;
use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::{TaskAssignment, TaskQueue};

/// Deliver a child session's turn output to its parent's inbox.
///
/// Records a `platform` event on the parent session (type "turn_complete")
/// and pushes a formatted message to the parent's task_queue. No-op if the
/// session has no parent (root lead).
pub async fn deliver_to_parent(
    db_pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    child_session_id: &str,
    turn_output: &str,
) -> Result<(), SchedulerError> {
    let child_session = db::sessions::get_by_id(db_pool, child_session_id).await?;

    let parent_id = match &child_session.parent_session_id {
        Some(pid) => pid.clone(),
        None => return Ok(()), // root lead — no parent
    };

    // Look up agent name for context prefix — fallback to agent_id
    let agent_name = match db::agents::get_by_id(db_pool, &child_session.agent_id).await {
        Ok(agent) => agent.name,
        Err(e) => {
            tracing::warn!(
                agent_id = %child_session.agent_id,
                error = %e,
                "failed to look up agent name for turn-complete prefix, using agent_id"
            );
            child_session.agent_id.clone()
        }
    };
    let agent_name = agent_name.replace(['\r', '\n'], " ");
    let agent_name = agent_name.trim();
    // Empty name after sanitization — fall back to agent_id
    let agent_name = if agent_name.is_empty() {
        &child_session.agent_id
    } else {
        agent_name
    };

    // Record platform event on parent session (audit + UI rendering).
    // Insert failure is non-fatal: delivery to task_queue is more important
    // than the audit trail. The parent agent must receive the child's output
    // even if the event store is temporarily unavailable.
    let parent_event = json!({
        "role": "agent",
        "parts": [{"kind": "data", "data": {
            "type": "turn_complete",
            "child_session_id": child_session_id,
            "message": turn_output,
        }}]
    });
    match db::events::insert(
        db_pool,
        &child_session.execution_id,
        Some(&parent_id),
        "platform",
        &serde_json::to_string(&parent_event).unwrap(),
    )
    .await
    {
        Ok(event_id) => {
            let _ = event_broadcast.send(EventNotification {
                execution_id: child_session.execution_id.clone(),
                event_id,
            });
        }
        Err(e) => {
            tracing::error!(
                child_session_id = %child_session_id,
                parent_id = %parent_id,
                error = %e,
                "failed to insert turn_complete platform event on parent"
            );
        }
    }

    // Format and push to parent's inbox
    let formatted_text = format!(
        "[turn complete from {} \u{00b7} session {}]\n\n{}",
        agent_name, child_session_id, turn_output
    );
    let delivery_payload = json!({
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": formatted_text}]
        },
    });
    task_queue
        .push(TaskAssignment {
            execution_id: child_session.execution_id.clone(),
            session_id: parent_id,
            task_payload: delivery_payload,
        })
        .await?;

    Ok(())
}

/// Extract human-readable text from turn messages, scanning from most recent.
///
/// Handles two formats:
/// 1. A2A: `{parts: [{kind: "text", text: "..."}]}`
/// 2. Claude API: `{content: [{type: "text", text: "..."}]}`
///
/// Returns None if no text could be extracted from any message.
pub fn extract_turn_output(turn_messages: &[TurnMessagePayload]) -> Option<String> {
    for msg in turn_messages.iter().rev() {
        if let Some(text) = extract_text_from_payload(&msg.payload) {
            return Some(text);
        }
    }
    None
}

fn extract_text_from_payload(payload: &serde_json::Value) -> Option<String> {
    // Try A2A format: {parts: [{kind: "text", text: "..."}]}
    if let Some(parts) = payload.get("parts").and_then(|p| p.as_array()) {
        let texts: Vec<&str> = parts
            .iter()
            .filter_map(|p| {
                if p.get("kind").and_then(|k| k.as_str()) == Some("text") {
                    p.get("text").and_then(|t| t.as_str())
                } else {
                    None
                }
            })
            .collect();
        if !texts.is_empty() {
            return Some(texts.join("\n"));
        }
    }

    // Try Claude API format: {content: [{type: "text", text: "..."}]}
    if let Some(content) = payload.get("content").and_then(|c| c.as_array()) {
        let texts: Vec<&str> = content
            .iter()
            .filter_map(|c| {
                if c.get("type").and_then(|t| t.as_str()) == Some("text") {
                    c.get("text").and_then(|t| t.as_str())
                } else {
                    None
                }
            })
            .collect();
        if !texts.is_empty() {
            return Some(texts.join("\n"));
        }
    }

    // Try plain string
    payload.as_str().map(|s| s.to_string())
}
