use std::collections::HashMap;

use axum::{Json, Router, extract::Query, extract::State, http::StatusCode, routing::get};
use serde::{Deserialize, Serialize};

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::services::messaging;

#[derive(Deserialize)]
pub struct DecisionsQuery {
    pub execution_id: Option<String>,
    pub status: Option<String>,
    pub include_terminal: Option<bool>,
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

    let events = db::events::list_all_platform_events(&state.db_pool).await?;

    // Phase 1: collect escalation events grouped by batch_id
    let mut batches: HashMap<String, PendingBatch> = HashMap::new();

    for event in &events {
        // Escalation events must be platform events from agents
        if event.event_type != "platform" {
            continue;
        }
        let payload: serde_json::Value = match serde_json::from_str(&event.payload) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let role = payload.get("role").and_then(|r| r.as_str()).unwrap_or("");
        if role != "ROLE_AGENT" {
            continue;
        }
        let parts = match payload.get("parts").and_then(|p| p.as_array()) {
            Some(p) => p,
            None => continue,
        };
        for part in parts {
            let data = match part.get("data") {
                Some(d) => d,
                None => continue,
            };
            let data_type = data.get("type").and_then(|t| t.as_str()).unwrap_or("");
            if data_type != "escalate" {
                continue;
            }
            let batch_id = match data.get("batch_id").and_then(|b| b.as_str()) {
                Some(b) => b.to_string(),
                None => continue,
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
                .entry(batch_id.clone())
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
            let event_session = event.session_id.as_deref().unwrap_or("");
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

    // Phase 2: scan for resolutions (answer/dismiss) — first-resolution-wins by event ID
    let mut resolutions: HashMap<String, (String, String, i64)> = HashMap::new(); // batch_id -> (type, timestamp, event_id)

    for event in &events {
        let payload: serde_json::Value = match serde_json::from_str(&event.payload) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let parts = match payload.get("parts").and_then(|p| p.as_array()) {
            Some(p) => p,
            None => continue,
        };

        // Defense-in-depth: skip events containing multiple question_answer parts.
        // The POST endpoint rejects these, but if one slips through, ignoring it
        // prevents a single event from resolving multiple batches.
        let qa_count = parts
            .iter()
            .filter(|p| {
                p.get("data")
                    .and_then(|d| d.get("type"))
                    .and_then(|t| t.as_str())
                    == Some("question_answer")
            })
            .count();
        if qa_count > 1 {
            continue;
        }

        for part in parts {
            let data = match part.get("data") {
                Some(d) => d,
                None => continue,
            };
            let data_type = data.get("type").and_then(|t| t.as_str()).unwrap_or("");
            if data_type != "question_answer" && data_type != "question_dismiss" {
                continue;
            }
            // Dismissals must come from platform events (the dismiss endpoint).
            // Reject question_dismiss in message events to prevent forged dismissals.
            if data_type == "question_dismiss" && event.event_type != "platform" {
                continue;
            }
            let batch_id = match data.get("batch_id").and_then(|b| b.as_str()) {
                Some(b) => b.to_string(),
                None => continue,
            };

            // First-resolution-wins: only store if not already resolved or this event is earlier
            if let Some(existing) = resolutions.get(&batch_id)
                && event.id >= existing.2
            {
                continue;
            }

            // For answers, extract the text from the text part of the message
            let answer_text = if data_type == "question_answer" {
                parts
                    .iter()
                    .find_map(|p| p.get("text").and_then(|t| t.as_str()))
                    .map(String::from)
            } else {
                None
            };

            resolutions.insert(
                batch_id,
                (
                    format!("{}|{}", data_type, answer_text.unwrap_or_default()),
                    event.created_at.to_rfc3339(),
                    event.id,
                ),
            );
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

        let resolution = resolutions.get(batch_id);
        let (status, answer, answered_at, dismissed_at) = match resolution {
            Some((type_and_answer, timestamp, _)) => {
                if type_and_answer.starts_with("question_answer") {
                    let answer_text = type_and_answer
                        .strip_prefix("question_answer|")
                        .unwrap_or("")
                        .to_string();
                    let answer_opt = if answer_text.is_empty() {
                        None
                    } else {
                        Some(answer_text)
                    };
                    (
                        "answered".to_string(),
                        answer_opt,
                        Some(timestamp.clone()),
                        None,
                    )
                } else {
                    ("dismissed".to_string(), None, None, Some(timestamp.clone()))
                }
            }
            None => {
                // Unresolved batches from terminal executions are expired, not actionable
                let status = if is_terminal {
                    "expired".to_string()
                } else {
                    "pending".to_string()
                };
                (status, None, None, None)
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
