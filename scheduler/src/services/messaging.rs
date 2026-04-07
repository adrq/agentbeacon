use std::collections::{HashMap, HashSet};
use std::sync::{Arc, RwLock};

use serde_json::json;
use tokio::sync::broadcast;

use crate::app::EventNotification;
use crate::db;
use crate::db::DbPool;
use crate::db::sessions::Session;
use crate::error::SchedulerError;
use crate::queue::TaskQueue;

/// Check if parts contain at least one deliverable content item:
/// a non-empty text part, a raw (inline binary) part, or a url part.
pub fn has_deliverable_content(parts: &[serde_json::Value]) -> bool {
    parts.iter().any(|p| {
        if let Some(text) = p.get("text").and_then(|t| t.as_str()) {
            !text.trim().is_empty()
        } else if let Some(raw) = p.get("raw").and_then(|r| r.as_str()) {
            !raw.is_empty()
        } else {
            p.get("url")
                .and_then(|u| u.as_str())
                .is_some_and(|u| !u.is_empty())
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

pub fn clear_stop_intent(_stop_turn_intents: &Arc<RwLock<HashSet<String>>>, _session_id: &str) {}

/// Core message delivery: route through transition function, record event.
/// Used by POST /api/messages (agent lateral messaging).
///
/// Routes through transition::Action::SendMessage.
#[allow(clippy::too_many_arguments)]
pub async fn deliver_message(
    db_pool: &DbPool,
    task_queue: &TaskQueue,
    event_broadcast: &broadcast::Sender<EventNotification>,
    _stop_turn_intents: &Arc<RwLock<HashSet<String>>>,
    session: &Session,
    parts: &[serde_json::Value],
    sender: Option<&SenderInfo>,
) -> Result<MessageDeliveryResult, SchedulerError> {
    let mut event_parts: Vec<serde_json::Value> = parts.to_vec();
    if let Some(s) = sender {
        event_parts.push(common::a2a::data_part(json!({
            "type": "sender",
            "name": s.name,
            "session_id": s.session_id,
        })));
    }
    let event_message = common::a2a::message_payload(common::a2a::role::USER, event_parts);

    let delivery_parts = if let Some(s) = sender {
        let header = format!(
            "[message from {} \u{00b7} session {}]\n\n",
            s.name, s.session_id
        );
        let mut dp: Vec<serde_json::Value> = parts.to_vec();
        if let Some(pos) = dp.iter().position(|p| p.get("text").is_some()) {
            let existing = dp[pos]["text"].as_str().unwrap_or("");
            dp[pos] = common::a2a::text_part(format!("{header}{existing}"));
        } else {
            dp.insert(0, common::a2a::text_part(header.trim_end()));
        }
        dp
    } else {
        parts.to_vec()
    };
    let delivery_payload = json!({
        "message": common::a2a::message_payload(common::a2a::role::USER, delivery_parts),
        "event_message": event_message
    });

    use crate::services::transition;
    let event_id = transition::transition(
        db_pool,
        &session.execution_id,
        &session.id,
        transition::Action::SendMessage(delivery_payload),
    )
    .await
    .map_err(|e| match e {
        transition::Rejected::WriteBarrier => {
            SchedulerError::Conflict("session or execution cannot accept messages".into())
        }
        other => SchedulerError::Database(format!("send message failed: {other:?}")),
    })?
    .unwrap_or(0);

    task_queue.wake_waiters();

    let _ = event_broadcast.send(EventNotification::persisted(
        session.execution_id.clone(),
        event_id,
    ));

    Ok(MessageDeliveryResult {
        event_id,
        session_status: "working".to_string(),
        execution_status: "working".to_string(),
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

    let max_depth = sessions.len();
    let mut result = Vec::new();
    for s in &sessions {
        let mut path_parts = Vec::new();
        let mut current_id = s.id.clone();
        for _ in 0..=max_depth {
            let Some((parent, slug)) = session_map.get(&current_id) else {
                break;
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

/// Like `hierarchical_name_for_session` but reads through an existing transaction.
pub async fn hierarchical_name_for_session_in_tx(
    db_pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    session_id: &str,
) -> Result<String, SchedulerError> {
    let mut path_parts = Vec::new();
    let mut current_id = session_id.to_string();

    for _ in 0..20 {
        let session = db::sessions::get_in_tx(db_pool, tx, &current_id).await?;
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
