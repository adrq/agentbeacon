use axum::{
    Json, Router,
    extract::{Query, State},
    http::StatusCode,
    response::IntoResponse,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::api::auth::McpSession;
use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::services::messaging::SenderInfo;

#[derive(Deserialize)]
struct SendMessageRequest {
    to: String,
    body: String,
}

#[derive(Deserialize)]
struct ListMessagesQuery {
    session_id: String,
    since_id: Option<i64>,
}

#[derive(Serialize)]
struct MessageResponse {
    id: i64,
    sender: Option<SenderResponse>,
    body: String,
    created_at: String,
}

#[derive(Serialize)]
struct SenderResponse {
    name: String,
    session_id: String,
}

/// POST /api/messages — agent sends message to another agent
///
/// Auth: Bearer session_id (McpSession extractor)
/// Body: { "to": "hierarchical/agent/name", "body": "message text" }
async fn send_message(
    auth: McpSession,
    State(state): State<AppState>,
    Json(req): Json<SendMessageRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let (recipient, sender_name) = crate::services::messaging::resolve_recipient_and_sender(
        &state.db_pool,
        &auth.execution_id,
        &req.to,
        &auth.session_id,
    )
    .await?;
    let sender_info = SenderInfo {
        name: sender_name,
        session_id: auth.session_id.clone(),
    };

    let result = crate::services::messaging::deliver_message(
        &state.db_pool,
        &state.task_queue,
        &state.event_broadcast,
        &recipient,
        &req.body,
        Some(&sender_info),
    )
    .await?;

    Ok((
        StatusCode::OK,
        Json(json!({
            "event_id": result.event_id,
            "recipient_session_id": recipient.id,
            "session_status": result.session_status,
        })),
    ))
}

/// GET /api/messages?session_id={id}&since_id={event_id} — message history
async fn list_messages(
    State(state): State<AppState>,
    Query(query): Query<ListMessagesQuery>,
) -> Result<Json<Vec<MessageResponse>>, SchedulerError> {
    db::sessions::get_by_id(&state.db_pool, &query.session_id).await?;

    let events =
        db::events::list_messages_by_session(&state.db_pool, &query.session_id, query.since_id)
            .await?;

    let messages: Vec<MessageResponse> = events
        .into_iter()
        .map(|e| {
            let payload: serde_json::Value = serde_json::from_str(&e.payload).unwrap_or_default();
            let sender = extract_sender_from_parts(&payload);
            let body = extract_text_from_parts(&payload);
            MessageResponse {
                id: e.id,
                sender,
                body,
                created_at: e.created_at.to_rfc3339(),
            }
        })
        .collect();

    Ok(Json(messages))
}

/// Extract sender metadata from A2A data part: {kind: "data", data: {type: "sender", ...}}
fn extract_sender_from_parts(payload: &serde_json::Value) -> Option<SenderResponse> {
    payload
        .get("parts")
        .and_then(|p| p.as_array())
        .and_then(|parts| {
            parts.iter().find(|p| {
                p.get("kind").and_then(|k| k.as_str()) == Some("data")
                    && p.get("data")
                        .and_then(|d| d.get("type"))
                        .and_then(|t| t.as_str())
                        == Some("sender")
            })
        })
        .and_then(|p| p.get("data"))
        .map(|d| SenderResponse {
            name: d["name"].as_str().unwrap_or("").to_string(),
            session_id: d["session_id"].as_str().unwrap_or("").to_string(),
        })
}

/// Extract text from A2A parts (skip data parts)
fn extract_text_from_parts(payload: &serde_json::Value) -> String {
    payload
        .get("parts")
        .and_then(|p| p.as_array())
        .map(|parts| {
            parts
                .iter()
                .filter_map(|p| {
                    if p.get("kind").and_then(|k| k.as_str()) == Some("text") {
                        p.get("text").and_then(|t| t.as_str())
                    } else {
                        None
                    }
                })
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

pub fn routes() -> Router<AppState> {
    Router::new().route(
        "/api/messages",
        axum::routing::post(send_message).get(list_messages),
    )
}
