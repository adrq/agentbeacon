use std::collections::{HashMap, HashSet};

use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use chrono::{DateTime, SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::api::problem::{Problem, ProblemCode};
use crate::api::types::PageV1;
use crate::api::versions::{DECISIONS_RESOLVED_DEFAULT_PAGE, DECISIONS_RESOLVED_MAX_PAGE};
use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::resolution::{self, ResolutionKind};

/// Rows read per chunk of the resolved-history walk.
const SCAN_CHUNK: i64 = 500;

/// Chunks one resolved-history request may read before returning a short page.
const SCAN_CHUNK_BUDGET: usize = 4;

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
    pub state: Option<String>,
    pub before: Option<String>,
    pub limit: Option<i64>,
}

#[derive(Serialize, Clone)]
pub struct DecisionQuestion {
    pub question: String,
    pub context: Option<String>,
    pub options: Option<Vec<QuestionOption>>,
    pub index: usize,
}

#[derive(Serialize, Clone)]
pub struct QuestionOption {
    pub label: String,
    pub description: Option<String>,
}

/// A decision with its full question set.
#[derive(Serialize)]
pub struct DecisionBrief {
    pub event_id: String,
    pub batch_id: String,
    pub execution_id: String,
    pub execution_title: Option<String>,
    pub session_id: String,
    pub status: String,
    pub importance: String,
    pub questions: Vec<DecisionQuestion>,
    pub answer: Option<String>,
    pub answered_at: Option<String>,
    pub dismissed_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub truncated: Option<bool>,
    pub created_at: String,
}

/// A resolved decision as it appears in history.
#[derive(Serialize)]
pub struct DecisionSummary {
    pub event_id: String,
    pub batch_id: String,
    pub execution_id: String,
    pub execution_title: Option<String>,
    pub session_id: String,
    pub status: String,
    pub importance: String,
    pub question_preview: String,
    pub question_count: usize,
    pub created_at: String,
}

#[derive(Serialize)]
pub struct DecisionsResponse {
    pub decisions: Vec<DecisionBrief>,
}

fn parse_questions(raw: &[serde_json::Value]) -> Vec<DecisionQuestion> {
    raw.iter()
        .enumerate()
        .map(|(index, q)| DecisionQuestion {
            question: q
                .get("question")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            context: q.get("context").and_then(|v| v.as_str()).map(String::from),
            options: q.get("options").and_then(|o| {
                o.as_array().map(|arr| {
                    arr.iter()
                        .filter_map(|opt| {
                            Some(QuestionOption {
                                label: opt.get("label")?.as_str()?.to_string(),
                                description: opt
                                    .get("description")
                                    .and_then(|d| d.as_str())
                                    .map(String::from),
                            })
                        })
                        .collect()
                })
            }),
            index,
        })
        .collect()
}

/// Assemble one decision's full representation.
fn build_brief(
    escalation: &db::events::Escalation,
    marker: Option<&resolution::ResolutionCandidate>,
    marker_created_at: Option<DateTime<Utc>>,
    execution_title: Option<String>,
    execution_terminal: bool,
) -> DecisionBrief {
    let (status, answer, answered_at, dismissed_at, truncated) = match marker {
        Some(cand) => match cand.kind {
            ResolutionKind::Answer => (
                "answered".to_string(),
                cand.answer_text.clone(),
                Some(normalize_resolved_at(
                    cand.resolved_at_raw.as_deref(),
                    marker_created_at.unwrap_or_else(Utc::now),
                )),
                None,
                if cand.truncated { Some(true) } else { None },
            ),
            ResolutionKind::Dismiss => (
                "dismissed".to_string(),
                None,
                None,
                Some(
                    marker_created_at
                        .unwrap_or_else(Utc::now)
                        .to_rfc3339_opts(SecondsFormat::Secs, true),
                ),
                None,
            ),
        },
        None if execution_terminal => ("expired".to_string(), None, None, None, None),
        None => ("pending".to_string(), None, None, None, None),
    };

    DecisionBrief {
        event_id: escalation.id.to_string(),
        batch_id: escalation.data.batch_id.clone(),
        execution_id: escalation.execution_id.clone(),
        execution_title,
        session_id: escalation.session_id.clone(),
        status,
        importance: escalation.data.importance.clone(),
        questions: parse_questions(&escalation.data.questions),
        answer,
        answered_at,
        dismissed_at,
        truncated,
        created_at: escalation.created_at.to_rfc3339(),
    }
}

/// The identity a marker must name to attach to an escalation.
#[derive(Hash, PartialEq, Eq)]
struct MarkerTarget {
    escalation_event_id: i64,
    execution_id: String,
    session_id: String,
}

/// The scan backing one pending-decisions response.
struct PendingScan {
    escalations: Vec<db::events::Escalation>,
    resolved: HashSet<MarkerTarget>,
}

impl PendingScan {
    fn pending_ids(&self) -> HashSet<i64> {
        self.escalations
            .iter()
            .filter(|e| {
                !self.resolved.contains(&MarkerTarget {
                    escalation_event_id: e.id,
                    execution_id: e.execution_id.clone(),
                    session_id: e.session_id.clone(),
                })
            })
            .map(|e| e.id)
            .collect()
    }
}

async fn scan_pending(
    state: &AppState,
    execution_ids: &[String],
) -> Result<PendingScan, SchedulerError> {
    let mut escalations = Vec::new();
    let mut resolved = HashSet::new();
    let events =
        db::events::list_platform_events_for_executions(&state.db_pool, execution_ids).await?;
    for window in events {
        if !window.session_coherent {
            continue;
        }
        let event = window.event;
        let Some(session_id) = event.session_id.clone() else {
            continue;
        };
        if let Some(data) = resolution::escalation_data(&event.payload) {
            escalations.push(db::events::Escalation {
                id: event.id,
                execution_id: event.execution_id,
                session_id,
                created_at: event.created_at,
                data,
            });
            continue;
        }
        let Some(parts) = resolution::parse_parts(&event.payload) else {
            continue;
        };
        for cand in resolution::resolution_candidates(&parts, event.id) {
            if let Some(escalation_event_id) = cand.escalation_event_id {
                resolved.insert(MarkerTarget {
                    escalation_event_id,
                    execution_id: event.execution_id.clone(),
                    session_id: session_id.clone(),
                });
            }
        }
    }
    Ok(PendingScan {
        escalations,
        resolved,
    })
}

/// Compute the validator for the pending decisions response.
fn pending_validator(executions: &[db::executions::ActiveExecutionMark]) -> String {
    let mut tuples: Vec<String> = executions
        .iter()
        .map(|execution| {
            format!(
                "{}\u{1f}{}\u{1f}{}\u{1f}{}",
                execution.id,
                execution.outcome.as_deref().unwrap_or(""),
                execution.desired,
                execution.platform_max.unwrap_or(0),
            )
        })
        .collect();
    tuples.sort();
    let mut hasher = Sha256::new();
    for tuple in &tuples {
        hasher.update(tuple.as_bytes());
        hasher.update([0x1e]);
    }
    format!("\"{}\"", hex::encode(hasher.finalize()))
}

/// RFC 9110 `If-None-Match`: `*` matches any current representation, and
/// entity-tags compare weakly, so a `W/` prefix is stripped from both sides.
fn header_matches(headers: &HeaderMap, etag: &str) -> bool {
    fn without_weak(value: &str) -> &str {
        value.strip_prefix("W/").unwrap_or(value)
    }
    headers
        .get(header::IF_NONE_MATCH)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|value| {
            let value = value.trim();
            if value == "*" {
                return true;
            }
            value
                .split(',')
                .any(|candidate| without_weak(candidate.trim()) == without_weak(etag))
        })
}

async fn pending_decisions(
    state: AppState,
    headers: HeaderMap,
    execution_filter: Option<String>,
) -> Result<Response, SchedulerError> {
    let mut executions = db::executions::list_active_with_platform_mark(&state.db_pool).await?;
    if let Some(ref filter) = execution_filter {
        executions.retain(|e| &e.id == filter);
    }

    let etag = pending_validator(&executions);
    let cache_headers = [
        (header::ETAG, etag.clone()),
        (header::CACHE_CONTROL, "no-store".to_string()),
    ];
    if header_matches(&headers, &etag) {
        return Ok((StatusCode::NOT_MODIFIED, cache_headers).into_response());
    }

    let ids: Vec<String> = executions.iter().map(|e| e.id.clone()).collect();
    let scan = scan_pending(&state, &ids).await?;
    let pending = scan.pending_ids();
    let titles: HashMap<&str, Option<String>> = executions
        .iter()
        .map(|e| (e.id.as_str(), e.title.clone()))
        .collect();

    let decisions = scan
        .escalations
        .iter()
        .filter(|e| pending.contains(&e.id))
        .map(|e| {
            build_brief(
                e,
                None,
                None,
                titles.get(e.execution_id.as_str()).cloned().flatten(),
                false,
            )
        })
        .collect();

    Ok((
        StatusCode::OK,
        cache_headers,
        Json(DecisionsResponse { decisions }),
    )
        .into_response())
}

async fn resolved_decisions(
    state: AppState,
    before: Option<String>,
    limit: Option<i64>,
) -> Result<Response, SchedulerError> {
    let invalid = |detail: &str| {
        SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::RequestInvalid).with_detail(detail.to_string()),
        ))
    };
    let limit = match limit {
        None => DECISIONS_RESOLVED_DEFAULT_PAGE,
        Some(n) if (1..=DECISIONS_RESOLVED_MAX_PAGE).contains(&n) => n,
        Some(_) => {
            return Err(invalid(
                "limit must be between 1 and the advertised maximum",
            ));
        }
    };
    let mut cursor = match before {
        None => None,
        Some(value) => Some(
            value
                .parse::<i64>()
                .ok()
                .filter(|v| *v > 0)
                .ok_or_else(|| invalid("cursor is not a valid event id"))?,
        ),
    };

    let prescan_pending = {
        let executions = db::executions::list_active(&state.db_pool).await?;
        let ids: Vec<String> = executions.iter().map(|e| e.id.clone()).collect();
        scan_pending(&state, &ids).await?.pending_ids()
    };

    let mut found: Vec<db::events::Escalation> = Vec::new();
    let mut lowest_inspected: Option<i64> = None;
    let mut exhausted = false;

    for _ in 0..SCAN_CHUNK_BUDGET {
        let chunk =
            db::events::scan_platform_descending(&state.db_pool, cursor, SCAN_CHUNK).await?;
        if chunk.scanned() == 0 {
            exhausted = true;
            break;
        }
        let short = (chunk.scanned() as i64) < SCAN_CHUNK;
        let chunk_len = chunk.scanned();
        let db::events::EventScan { events, ids } = chunk;
        let mut decoded: HashMap<i64, db::events::Event> =
            events.into_iter().map(|e| (e.id, e)).collect();
        let mut inspected = 0usize;
        for id in ids {
            inspected += 1;
            lowest_inspected = Some(id);
            cursor = Some(id);
            let Some(event) = decoded.remove(&id) else {
                continue;
            };
            let is_escalation = match (
                event.session_id.clone(),
                resolution::escalation_data(&event.payload),
            ) {
                (Some(session_id), Some(data)) if !prescan_pending.contains(&event.id) => {
                    found.push(db::events::Escalation {
                        id: event.id,
                        execution_id: event.execution_id,
                        session_id,
                        created_at: event.created_at,
                        data,
                    });
                    true
                }
                _ => false,
            };
            if is_escalation && found.len() as i64 >= limit {
                break;
            }
        }
        if inspected < chunk_len {
            break;
        }
        if short {
            exhausted = true;
            break;
        }
        if found.len() as i64 >= limit {
            break;
        }
    }

    let executions = db::executions::list_active(&state.db_pool).await?;
    let ids: Vec<String> = executions.iter().map(|e| e.id.clone()).collect();
    let pending = scan_pending(&state, &ids).await?.pending_ids();
    found.retain(|e| !pending.contains(&e.id));

    let title_ids: Vec<String> = found
        .iter()
        .map(|e| e.execution_id.clone())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    let titles = db::executions::titles_for(&state.db_pool, &title_ids).await?;

    let items: Vec<DecisionSummary> = found
        .iter()
        .map(|e| {
            let questions = parse_questions(&e.data.questions);
            DecisionSummary {
                event_id: e.id.to_string(),
                batch_id: e.data.batch_id.clone(),
                execution_id: e.execution_id.clone(),
                execution_title: titles.get(&e.execution_id).cloned().flatten(),
                session_id: e.session_id.clone(),
                status: "resolved".to_string(),
                importance: e.data.importance.clone(),
                question_preview: questions
                    .first()
                    .map(|q| q.question.clone())
                    .unwrap_or_default(),
                question_count: questions.len(),
                created_at: e.created_at.to_rfc3339(),
            }
        })
        .collect();

    let has_more = match lowest_inspected {
        Some(_) if exhausted => false,
        Some(id) => db::events::platform_rows_exist_below(&state.db_pool, id).await?,
        None => false,
    };
    let next_cursor = if has_more {
        lowest_inspected.map(|id| id.to_string())
    } else {
        None
    };

    Ok(Json(PageV1 {
        items,
        next_cursor,
        has_more,
    })
    .into_response())
}

async fn get_decisions(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<DecisionsQuery>,
) -> Result<Response, SchedulerError> {
    match params.state.as_deref() {
        None => {
            if params.before.is_some() {
                return Err(SchedulerError::Problem(Box::new(
                    Problem::new(ProblemCode::RequestInvalid)
                        .with_detail("before is only valid with state=resolved"),
                )));
            }
            if params.limit.is_some() {
                return Err(SchedulerError::Problem(Box::new(
                    Problem::new(ProblemCode::RequestInvalid)
                        .with_detail("limit is only valid with state=resolved"),
                )));
            }
            pending_decisions(state, headers, params.execution_id).await
        }
        Some("resolved") => {
            if params.execution_id.is_some() {
                return Err(SchedulerError::Problem(Box::new(
                    Problem::new(ProblemCode::RequestInvalid)
                        .with_detail("execution_id is only valid without state=resolved"),
                )));
            }
            resolved_decisions(state, params.before, params.limit).await
        }
        Some(_) => Err(SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::RequestInvalid).with_detail("unknown state"),
        ))),
    }
}

async fn get_decision(
    State(state): State<AppState>,
    Path(event_id): Path<String>,
) -> Result<Json<DecisionBrief>, SchedulerError> {
    let not_found = || {
        SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::DecisionNotFound).with_detail("no decision has this id"),
        ))
    };
    let event_id: i64 = event_id.parse().map_err(|_| not_found())?;
    let escalation = db::events::get_escalation(&state.db_pool, event_id)
        .await?
        .ok_or_else(not_found)?;

    let mut tx =
        db::executions::begin_execution_tx(&state.db_pool, &escalation.execution_id).await?;
    let brief = async {
        let escalation = db::events::get_escalation_in_tx(&state.db_pool, &mut tx, event_id)
            .await?
            .ok_or_else(not_found)?;
        let execution =
            db::executions::get_in_tx(&state.db_pool, &mut tx, &escalation.execution_id).await?;
        let terminal = execution.outcome.is_some() || execution.desired == "terminate";

        let marker =
            db::events::find_marker_for_escalation_in_tx(&state.db_pool, &mut tx, &escalation)
                .await?;
        let marker_created_at = match &marker {
            Some(cand) => {
                db::events::get_by_id_in_tx(&state.db_pool, &mut tx, cand.order_key.own_event_id)
                    .await?
                    .map(|e| e.created_at)
            }
            None => None,
        };

        Ok::<DecisionBrief, SchedulerError>(build_brief(
            &escalation,
            marker.as_ref(),
            marker_created_at,
            execution.title,
            terminal,
        ))
    }
    .await;

    match brief {
        Ok(brief) => {
            tx.commit()
                .await
                .map_err(|e| SchedulerError::Database(format!("decision detail: {e}")))?;
            Ok(Json(brief))
        }
        Err(e) => {
            let _ = tx.rollback().await;
            Err(e)
        }
    }
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/decisions", get(get_decisions))
        .route("/decisions/{event_id}", get(get_decision))
}
