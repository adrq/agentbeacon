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
    description: String,
}

#[derive(Serialize)]
pub struct EscalateResponse {
    question_ids: Vec<i64>,
    batch_id: String,
}

async fn escalate(
    auth: McpSession,
    State(state): State<AppState>,
    body: Result<Json<EscalateRequest>, JsonRejection>,
) -> Result<impl IntoResponse, SchedulerError> {
    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;
    // Root-lead only
    if auth.role != McpRole::RootLead {
        return Err(SchedulerError::Forbidden(
            "escalate is only available to the root lead agent".to_string(),
        ));
    }

    // Validate questions count: 1-4
    if req.questions.is_empty() || req.questions.len() > 4 {
        return Err(SchedulerError::ValidationFailed(
            "questions must contain 1-4 items".to_string(),
        ));
    }

    // Validate importance
    if req.importance != "blocking" && req.importance != "fyi" {
        return Err(SchedulerError::ValidationFailed(format!(
            "importance must be \"blocking\" or \"fyi\", got \"{}\"",
            req.importance
        )));
    }

    // Validate options per question
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
    let batch_size = req.questions.len();
    let mut question_ids = Vec::with_capacity(batch_size);

    for (batch_index, q) in req.questions.iter().enumerate() {
        let mut data = json!({
            "type": "escalate",
            "question": q.question,
            "importance": req.importance,
            "batch_id": batch_id,
            "batch_size": batch_size,
            "batch_index": batch_index,
        });
        if let Some(ref opts) = q.options {
            data["options"] = serde_json::to_value(opts).unwrap_or_default();
        }
        if let Some(ref ctx) = q.context {
            data["context"] = json!(ctx);
        }
        let event_payload = json!({
            "role": "ROLE_AGENT",
            "parts": [{"data": data}]
        });

        let event_id = db::events::insert(
            &state.db_pool,
            &auth.execution_id,
            Some(&auth.session_id),
            "platform",
            &serde_json::to_string(&event_payload).unwrap(),
        )
        .await
        .map_err(|e| SchedulerError::Database(e.to_string()))?;

        let _ = state.event_broadcast.send(EventNotification::persisted(
            auth.execution_id.clone(),
            event_id,
        ));

        question_ids.push(event_id);
    }

    Ok((
        StatusCode::OK,
        Json(EscalateResponse {
            question_ids,
            batch_id,
        }),
    ))
}

async fn dismiss(
    headers: HeaderMap,
    Path(batch_id): Path<String>,
    State(state): State<AppState>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Reject MCP session auth — dismiss is a user-initiated UI action only
    if let Some(auth) = headers.get("authorization").and_then(|v| v.to_str().ok())
        && auth.len() > 7
        && auth[..7].eq_ignore_ascii_case("bearer ")
    {
        let token = &auth[7..];
        if db::sessions::get_by_id(&state.db_pool, token).await.is_ok() {
            return Err(SchedulerError::Forbidden(
                "dismiss is not available via agent session auth".to_string(),
            ));
        }
    }

    // Find escalation events for this batch_id to validate it exists and get context
    let batch_events = db::events::find_by_batch_id(&state.db_pool, &batch_id).await?;
    if batch_events.is_empty() {
        return Err(SchedulerError::NotFound(format!(
            "batch_id {batch_id} not found"
        )));
    }

    let first_event = &batch_events[0];
    let execution_id = &first_event.execution_id;
    let session_id = first_event.session_id.as_deref();

    // Best-effort pre-checks — the reducer handles rare race duplicates
    let execution = db::executions::get_by_id(&state.db_pool, execution_id).await?;
    if execution.outcome.is_some() || execution.desired == "terminate" {
        return Ok((
            StatusCode::OK,
            Json(json!({"status": "expired", "message": "execution is terminal"})),
        ));
    }

    if let Some(resolution) =
        db::events::find_resolution_for_batch(&state.db_pool, &batch_id).await?
    {
        if resolution.resolution_type == "question_dismiss" {
            return Ok((StatusCode::OK, Json(json!({"status": "already_dismissed"}))));
        } else {
            return Ok((StatusCode::OK, Json(json!({"status": "already_resolved"}))));
        }
    }

    // Simple event write — no lock, no transaction. Rare race duplicates are
    // harmless; the reducer (GET /api/decisions) applies first-resolution-wins.
    let dismiss_data = json!({"type": "question_dismiss", "batch_id": batch_id});
    let event_payload = json!({
        "role": "ROLE_USER",
        "parts": [{"data": dismiss_data}]
    });
    let event_id = db::events::insert(
        &state.db_pool,
        execution_id,
        session_id,
        "platform",
        &serde_json::to_string(&event_payload).unwrap(),
    )
    .await?;

    let _ = state
        .event_broadcast
        .send(EventNotification::persisted(execution_id.clone(), event_id));
    Ok((StatusCode::OK, Json(json!({"status": "dismissed"}))))
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/escalate", post(escalate))
        .route("/api/escalate/{batch_id}/dismiss", post(dismiss))
}
