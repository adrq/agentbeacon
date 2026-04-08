use axum::{
    Json, Router, extract::State, extract::rejection::JsonRejection, http::StatusCode,
    response::IntoResponse, routing::post,
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

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/escalate", post(escalate))
}
