// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::{
    Json, Router,
    extract::Path,
    extract::State,
    extract::rejection::JsonRejection,
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::post,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::api::auth::{McpRole, McpSession};
use crate::api::problem::{Problem, ProblemCode};
use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;

fn default_importance() -> String {
    "blocking".to_string()
}

#[derive(Deserialize)]
pub struct EscalateRequest {
    questions: Vec<EscalateQuestion>,
    #[serde(default = "default_importance")]
    importance: String,
}

#[derive(Deserialize)]
pub struct EscalateQuestion {
    question: String,
    context: Option<String>,
    options: Option<Vec<EscalateOption>>,
}

#[derive(Deserialize, Serialize)]
pub struct EscalateOption {
    label: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    description: Option<String>,
}

#[derive(Serialize)]
pub struct EscalateResponse {
    batch_id: String,
    event_id: String,
}

async fn escalate(
    auth: McpSession,
    State(state): State<AppState>,
    body: Result<Json<EscalateRequest>, JsonRejection>,
) -> Result<impl IntoResponse, SchedulerError> {
    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;
    if auth.role != McpRole::RootLead {
        return Err(SchedulerError::Forbidden(
            "escalate is only available to the root lead agent".to_string(),
        ));
    }

    if req.questions.is_empty() || req.questions.len() > 4 {
        return Err(SchedulerError::ValidationFailed(
            "questions must contain 1-4 items".to_string(),
        ));
    }

    if req.importance != "blocking" && req.importance != "fyi" {
        return Err(SchedulerError::ValidationFailed(format!(
            "importance must be \"blocking\" or \"fyi\", got \"{}\"",
            req.importance
        )));
    }

    for q in &req.questions {
        if let Some(ref opts) = q.options
            && (opts.len() < 2 || opts.len() > 5)
        {
            return Err(SchedulerError::ValidationFailed(
                "options must contain 2-5 items".to_string(),
            ));
        }
    }

    let batch_id = Uuid::new_v4().to_string();

    let questions: Vec<serde_json::Value> = req
        .questions
        .iter()
        .map(|q| {
            let mut entry = json!({"question": q.question});
            if let Some(ref ctx) = q.context {
                entry["context"] = json!(ctx);
            }
            if let Some(ref opts) = q.options {
                entry["options"] = serde_json::to_value(opts).unwrap_or_default();
            }
            entry
        })
        .collect();

    let event_payload = json!({
        "role": crate::resolution::ROLE_AGENT,
        "parts": [{"data": {
            "type": crate::resolution::ESCALATE_TYPE,
            "batch_id": batch_id,
            "importance": req.importance,
            "questions": questions,
        }}]
    });

    let mut tx = db::executions::begin_execution_tx(&state.db_pool, &auth.execution_id).await?;
    let inserted = match db::events::insert_in_tx(
        &state.db_pool,
        &mut tx,
        Some(&auth.session_id),
        "platform",
        &serde_json::to_string(&event_payload).unwrap(),
    )
    .await
    {
        Ok(inserted) => inserted,
        Err(e) => {
            let _ = tx.rollback().await;
            return Err(e);
        }
    };
    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit escalate tx: {e}")))?;

    let _ = state
        .event_broadcast
        .send(EventNotification::persisted(auth.execution_id.clone(), 0));

    Ok((
        StatusCode::OK,
        Json(EscalateResponse {
            batch_id,
            event_id: inserted.id.to_string(),
        }),
    ))
}
/// Build the `resolution` extension attached to `decision.already_resolved`.
pub fn resolution_extension(cand: &crate::resolution::ResolutionCandidate) -> serde_json::Value {
    let mut ext = json!({
        "kind": match cand.kind {
            crate::resolution::ResolutionKind::Answer => "answered",
            crate::resolution::ResolutionKind::Dismiss => "dismissed",
        },
    });
    if let Some(ref text) = cand.answer_text {
        ext["answer_text"] = json!(text);
    }
    if let Some(ref at) = cand.resolved_at_raw {
        ext["resolved_at"] = json!(at);
    }
    ext
}

fn already_resolved(cand: &crate::resolution::ResolutionCandidate) -> SchedulerError {
    SchedulerError::Problem(Box::new(
        Problem::new(ProblemCode::DecisionAlreadyResolved)
            .with_detail("this decision was already resolved")
            .with_extension("resolution", resolution_extension(cand)),
    ))
}

async fn dismiss(
    headers: HeaderMap,
    Path(event_id): Path<String>,
    State(state): State<AppState>,
) -> Result<impl IntoResponse, SchedulerError> {
    if crate::api::sessions::is_agent_session_bearer(&headers, &state).await? {
        return Err(SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::AuthForbidden)
                .with_detail("dismiss is not available via agent session auth"),
        )));
    }

    let decision_not_found = || {
        SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::DecisionNotFound).with_detail("no decision has this id"),
        ))
    };

    let event_id: i64 = event_id.parse().map_err(|_| decision_not_found())?;
    let escalation = db::events::get_escalation(&state.db_pool, event_id)
        .await?
        .ok_or_else(decision_not_found)?;

    let mut tx = db::executions::begin_execution_tx(&state.db_pool, &escalation.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin dismiss tx: {e}")))?;

    let outcome = dismiss_in_tx(&state, &mut tx, &escalation).await;
    let inserted = match outcome {
        Ok(inserted) => {
            tx.commit()
                .await
                .map_err(|e| SchedulerError::Database(format!("commit dismiss tx: {e}")))?;
            inserted
        }
        Err(e) => {
            let _ = tx.rollback().await;
            return Err(e);
        }
    };

    let _ = state.event_broadcast.send(EventNotification::persisted(
        escalation.execution_id.clone(),
        inserted.id,
    ));
    Ok((StatusCode::OK, Json(json!({"status": "dismissed"}))))
}

async fn dismiss_in_tx(
    state: &AppState,
    tx: &mut db::executions::ExecutionTx<'_>,
    escalation: &db::events::Escalation,
) -> Result<db::events::InsertedEvent, SchedulerError> {
    if let Some(cand) =
        db::events::find_marker_for_escalation_in_tx(&state.db_pool, tx, escalation).await?
    {
        return Err(already_resolved(&cand));
    }

    let tx_exec = db::executions::get_in_tx(&state.db_pool, tx, &escalation.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck execution: {e}")))?;
    if tx_exec.outcome.is_some() || tx_exec.desired == "terminate" {
        return Err(SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::DecisionExpired)
                .with_detail("the execution this decision belongs to has ended"),
        )));
    }

    let event_payload =
        crate::resolution::dismiss_payload(&escalation.data.batch_id, escalation.id);
    db::events::insert_in_tx(
        &state.db_pool,
        tx,
        Some(&escalation.session_id),
        "platform",
        &serde_json::to_string(&event_payload).unwrap(),
    )
    .await
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/escalate", post(escalate))
        .route("/escalate/{event_id}/dismiss", post(dismiss))
}
