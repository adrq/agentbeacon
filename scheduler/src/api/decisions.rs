use std::collections::HashMap;

use axum::{Json, Router, extract::Query, extract::State, http::StatusCode, routing::get};
use chrono::{DateTime, SecondsFormat, Utc};
use serde::{Deserialize, Serialize};

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::resolution::{self, OrderKey, ResolutionKind};
use crate::services::messaging;

/// Normalize a timestamp to UTC RFC3339 with a `Z` suffix; use `fallback` when `raw` is
/// absent or not valid RFC3339.
fn normalize_resolved_at(raw: Option<&str>, fallback: DateTime<Utc>) -> String {
    let dt = raw
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|d| d.with_timezone(&Utc))
        .unwrap_or(fallback);
    dt.to_rfc3339_opts(SecondsFormat::Secs, true)
}

#[derive(Deserialize)]
pub struct DecisionsQuery {
    pub execution_id: Option<String>,
    pub status: Option<String>,
    pub include_terminal: Option<bool>,
    pub resolved_limit: Option<i64>,
}

#[derive(Serialize, Clone)]
pub struct DecisionQuestion {
    pub question: String,
    pub context: Option<String>,
    pub options: Option<Vec<QuestionOption>>,
    pub batch_index: usize,
}

#[derive(Serialize, Clone)]
pub struct QuestionOption {
    pub label: String,
    pub description: String,
}

#[derive(Serialize)]
pub struct DecisionBatch {
    pub batch_id: String,
    pub execution_id: String,
    pub execution_title: Option<String>,
    pub session_id: String,
    pub agent_name: String,
    pub hierarchical_name: String,
    pub status: String,
    pub importance: String,
    pub questions: Vec<DecisionQuestion>,
    pub answer: Option<String>,
    pub answered_at: Option<String>,
    pub dismissed_at: Option<String>,
    /// Present (and true) only when this answer's text was truncated; absent otherwise.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub truncated: Option<bool>,
    pub created_at: String,
}

#[derive(Serialize)]
pub struct DecisionsResponse {
    pub decisions: Vec<DecisionBatch>,
}

struct PendingBatch {
    execution_id: String,
    session_id: String,
    batch_size: usize,
    importance: String,
    questions: Vec<DecisionQuestion>,
    created_at: String,
    min_event_id: i64,
}

async fn get_decisions(
    State(state): State<AppState>,
    Query(params): Query<DecisionsQuery>,
) -> Result<(StatusCode, Json<DecisionsResponse>), SchedulerError> {
    let include_terminal = params.include_terminal.unwrap_or(true);
    let resolved_limit = params.resolved_limit.unwrap_or(20).max(0);

    // Fast path: specific execution_id requested — query that single execution directly
    let mut events = if let Some(ref exec_id) = params.execution_id {
        db::events::list_platform_events_for_executions(
            &state.db_pool,
            std::slice::from_ref(exec_id),
        )
        .await?
    } else {
        // Main path: get active execution events
        let active_ids = db::executions::list_active_ids(&state.db_pool).await?;
        let mut all_events =
            db::events::list_platform_events_for_executions(&state.db_pool, &active_ids).await?;

        // Optionally add recent terminal execution events
        if include_terminal && resolved_limit > 0 {
            let terminal_ids =
                db::executions::list_recent_terminal_ids(&state.db_pool, resolved_limit).await?;
            let terminal_events =
                db::events::list_platform_events_for_executions(&state.db_pool, &terminal_ids)
                    .await?;
            all_events.extend(terminal_events);
        }

        all_events
    };

    events.sort_by_key(|we| we.event.id);

    let mut batches: HashMap<String, PendingBatch> = HashMap::new();

    for we in &events {
        let event = &we.event;
        if !we.session_coherent {
            continue;
        }
        let escalates = resolution::escalate_parts(&event.payload);
        if escalates.is_empty() {
            continue;
        }
        let payload: serde_json::Value = match serde_json::from_str(&event.payload) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let parts = payload.get("parts").and_then(|p| p.as_array());
        let event_session = event.session_id.as_deref().unwrap_or("");

        for ep in escalates {
            let Some(data) = parts
                .and_then(|ps| ps.get(ep.part_ordinal as usize))
                .and_then(|p| p.get("data"))
            else {
                continue;
            };
            let batch_size = data.get("batch_size").and_then(|s| s.as_u64()).unwrap_or(1) as usize;
            let batch_index = data
                .get("batch_index")
                .and_then(|i| i.as_u64())
                .unwrap_or(0) as usize;
            let importance = data
                .get("importance")
                .and_then(|i| i.as_str())
                .unwrap_or("blocking")
                .to_string();
            let question_text = data
                .get("question")
                .and_then(|q| q.as_str())
                .unwrap_or("")
                .to_string();
            let context = data
                .get("context")
                .and_then(|c| c.as_str())
                .map(String::from);
            let options: Option<Vec<QuestionOption>> = data.get("options").and_then(|o| {
                o.as_array().map(|arr| {
                    arr.iter()
                        .filter_map(|opt| {
                            Some(QuestionOption {
                                label: opt.get("label")?.as_str()?.to_string(),
                                description: opt.get("description")?.as_str()?.to_string(),
                            })
                        })
                        .collect()
                })
            });

            let entry = batches
                .entry(ep.batch_id.clone())
                .or_insert_with(|| PendingBatch {
                    execution_id: event.execution_id.clone(),
                    session_id: event.session_id.clone().unwrap_or_default(),
                    batch_size,
                    importance: importance.clone(),
                    questions: Vec::new(),
                    created_at: event.created_at.to_rfc3339(),
                    min_event_id: event.id,
                });

            // Ignore events with conflicting batch_size or session_id
            if entry.batch_size != batch_size || entry.session_id != event_session {
                continue;
            }

            if event.id < entry.min_event_id {
                entry.min_event_id = event.id;
                entry.created_at = event.created_at.to_rfc3339();
            }

            // Deduplicate by batch_index — only keep first event for each index
            if !entry.questions.iter().any(|q| q.batch_index == batch_index) {
                entry.questions.push(DecisionQuestion {
                    question: question_text,
                    context,
                    options,
                    batch_index,
                });
            }
        }
    }

    struct WinningResolution {
        order_key: OrderKey,
        kind: ResolutionKind,
        answer_text: Option<String>,
        resolved_at_raw: Option<String>,
        truncated: bool,
        marker_created_at: DateTime<Utc>,
    }
    let mut resolutions: HashMap<String, WinningResolution> = HashMap::new();

    for we in &events {
        let event = &we.event;
        let Some(parts) = resolution::parse_parts(&event.payload) else {
            continue;
        };
        for cand in resolution::resolution_candidates(&parts, event.id) {
            let Some(owner) = batches.get(&cand.batch_id) else {
                continue;
            };
            // Exact identity match: never collapse a NULL session into an empty-string owner.
            if event.execution_id != owner.execution_id
                || event.session_id.as_deref() != Some(owner.session_id.as_str())
            {
                continue;
            }
            let wins = resolutions
                .get(&cand.batch_id)
                .is_none_or(|existing| cand.order_key < existing.order_key);
            if wins {
                resolutions.insert(
                    cand.batch_id.clone(),
                    WinningResolution {
                        order_key: cand.order_key,
                        kind: cand.kind,
                        answer_text: cand.answer_text.clone(),
                        resolved_at_raw: cand.resolved_at_raw.clone(),
                        truncated: cand.truncated,
                        marker_created_at: event.created_at,
                    },
                );
            }
        }
    }

    // Phase 3: load execution and session metadata for enrichment
    let execution_ids: Vec<String> = batches.values().map(|b| b.execution_id.clone()).collect();
    let mut exec_map: HashMap<String, (Option<String>, Option<String>, String)> = HashMap::new(); // id -> (title, outcome, desired)
    for exec_id in &execution_ids {
        if exec_map.contains_key(exec_id) {
            continue;
        }
        if let Ok(exec) = db::executions::get_by_id(&state.db_pool, exec_id).await {
            exec_map.insert(exec_id.clone(), (exec.title, exec.outcome, exec.desired));
        }
    }

    let session_ids: Vec<String> = batches.values().map(|b| b.session_id.clone()).collect();
    let mut session_map: HashMap<String, (String, String)> = HashMap::new(); // id -> (agent_name, slug)
    for sid in &session_ids {
        if sid.is_empty() || session_map.contains_key(sid) {
            continue;
        }
        if let Ok(session) = db::sessions::get_by_id(&state.db_pool, sid).await {
            let agent_name =
                if let Ok(agent) = db::agents::get_by_id(&state.db_pool, &session.agent_id).await {
                    agent.name
                } else {
                    session.agent_id[..8.min(session.agent_id.len())].to_string()
                };
            let hier_name = messaging::hierarchical_name_for_session(&state.db_pool, sid)
                .await
                .unwrap_or_else(|_| session.slug.clone());
            session_map.insert(sid.clone(), (agent_name, hier_name));
        }
    }

    // Phase 4: assemble decision batches
    let mut decisions: Vec<DecisionBatch> = Vec::new();

    for (batch_id, batch) in &batches {
        // Completeness gate: unique question count must match batch_size, and
        // indices must be contiguous 0..batch_size-1
        if batch.questions.len() != batch.batch_size {
            continue;
        }
        let mut indices: Vec<usize> = batch.questions.iter().map(|q| q.batch_index).collect();
        indices.sort_unstable();
        let expected: Vec<usize> = (0..batch.batch_size).collect();
        if indices != expected {
            continue;
        }

        // Filter by execution_id if specified
        if let Some(ref filter_exec) = params.execution_id
            && &batch.execution_id != filter_exec
        {
            continue;
        }

        // Filter terminal executions (outcome set OR desired=terminate)
        let exec_info = exec_map.get(&batch.execution_id);
        let is_terminal = exec_info
            .is_some_and(|(_, outcome, desired)| outcome.is_some() || desired == "terminate");
        if !include_terminal && is_terminal {
            continue;
        }

        let (status, answer, answered_at, dismissed_at, truncated) = match resolutions.get(batch_id)
        {
            Some(res) => match res.kind {
                ResolutionKind::Answer => {
                    let answered_at = normalize_resolved_at(
                        res.resolved_at_raw.as_deref(),
                        res.marker_created_at,
                    );
                    // Only surface truncation when it happened (absent => false).
                    let truncated = if res.truncated { Some(true) } else { None };
                    (
                        "answered".to_string(),
                        res.answer_text.clone(),
                        Some(answered_at),
                        None,
                        truncated,
                    )
                }
                ResolutionKind::Dismiss => {
                    // Dismissals have no embedded timestamp; use the event's created_at.
                    let dismissed_at = res
                        .marker_created_at
                        .to_rfc3339_opts(SecondsFormat::Secs, true);
                    (
                        "dismissed".to_string(),
                        None,
                        None,
                        Some(dismissed_at),
                        None,
                    )
                }
            },
            None => {
                // Unresolved batches from terminal executions are expired, not actionable
                let status = if is_terminal {
                    "expired".to_string()
                } else {
                    "pending".to_string()
                };
                (status, None, None, None, None)
            }
        };

        // Filter by status if specified
        if let Some(ref filter_status) = params.status
            && &status != filter_status
        {
            continue;
        }

        let exec_title = exec_info.and_then(|(title, _, _)| title.clone());
        let (agent_name, hierarchical_name) = session_map
            .get(&batch.session_id)
            .map(|(name, slug)| (name.clone(), slug.clone()))
            .unwrap_or_else(|| ("unknown".to_string(), "unknown".to_string()));

        let mut questions = batch.questions.clone();
        questions.sort_by_key(|q| q.batch_index);

        decisions.push(DecisionBatch {
            batch_id: batch_id.clone(),
            execution_id: batch.execution_id.clone(),
            execution_title: exec_title,
            session_id: batch.session_id.clone(),
            agent_name,
            hierarchical_name,
            status,
            importance: batch.importance.clone(),
            questions,
            answer,
            answered_at,
            dismissed_at,
            truncated,
            created_at: batch.created_at.clone(),
        });
    }

    // Sort by min_event_id (monotonic, guarantees stable ordering even within the same second)
    // Build a lookup from batch_id -> min_event_id for sorting
    let batch_order: HashMap<String, i64> = batches
        .iter()
        .map(|(bid, b)| (bid.clone(), b.min_event_id))
        .collect();
    decisions.sort_by_key(|d| batch_order.get(&d.batch_id).copied().unwrap_or(0));

    Ok((StatusCode::OK, Json(DecisionsResponse { decisions })))
}

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/decisions", get(get_decisions))
}
