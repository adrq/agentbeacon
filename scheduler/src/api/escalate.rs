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
use sqlx::Row;
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

    // An empty batch_id can never own a batch; reject before the owner lookup (its LIKE
    // pattern would otherwise match every payload).
    if batch_id.is_empty() {
        return Err(SchedulerError::NotFound(format!(
            "batch {batch_id} does not exist"
        )));
    }

    let owner = db::events::find_batch_owner(&state.db_pool, &batch_id)
        .await?
        .ok_or_else(|| SchedulerError::NotFound(format!("batch {batch_id} does not exist")))?;

    // Open a transaction on the owner execution for the checks and insert below.
    let mut tx = db::executions::begin_execution_tx(&state.db_pool, &owner.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin dismiss tx: {e}")))?;

    let tx_exec = db::executions::get_in_tx(&state.db_pool, &mut tx, &owner.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck execution: {e}")))?;
    if tx_exec.outcome.is_some() || tx_exec.desired == "terminate" {
        let _ = tx.rollback().await;
        return Ok((
            StatusCode::OK,
            Json(json!({"status": "expired", "message": "execution is terminal"})),
        ));
    }

    if let Some(resolution) = db::events::find_resolution_for_batch_in_tx(
        &state.db_pool,
        &mut tx,
        &owner.execution_id,
        &owner.session_id,
        &batch_id,
    )
    .await?
    {
        let _ = tx.rollback().await;
        let status = match resolution.kind {
            crate::resolution::ResolutionKind::Dismiss => "already_dismissed",
            crate::resolution::ResolutionKind::Answer => "already_resolved",
        };
        return Ok((StatusCode::OK, Json(json!({"status": status}))));
    }

    let event_payload = json!({
        "role": "ROLE_USER",
        "parts": [{"data": {"type": "question_dismiss", "batch_id": batch_id}}]
    });
    let event_sql = state.db_pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) \
         VALUES (?, ?, 'platform', ?) RETURNING id",
    );
    let event_id: i64 = sqlx::query(&event_sql)
        .bind(&owner.execution_id)
        .bind(&owner.session_id)
        .bind(serde_json::to_string(&event_payload).unwrap())
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("insert dismiss failed: {e}")))?
        .try_get("id")
        .unwrap_or(0);

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit dismiss tx: {e}")))?;

    let _ = state.event_broadcast.send(EventNotification::persisted(
        owner.execution_id.clone(),
        event_id,
    ));
    Ok((StatusCode::OK, Json(json!({"status": "dismissed"}))))
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/escalate", post(escalate))
        .route("/api/escalate/{batch_id}/dismiss", post(dismiss))
}
