use std::convert::Infallible;
use std::time::Duration;

use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::routing::get;
use axum::{Router, response::IntoResponse};
use tokio::sync::broadcast;

use crate::api::types::EventResponse;
use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

/// SSE route: `GET /api/executions/{id}/events/stream`
pub fn routes() -> Router<AppState> {
    Router::new().route(
        "/api/executions/{id}/events/stream",
        get(execution_event_stream),
    )
}

async fn execution_event_stream(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, SchedulerError> {
    // Verify execution exists (404 if not)
    db::executions::get_by_id(&state.db_pool, &id).await?;

    // Parse Last-Event-ID header for reconnection support
    let since_id: i64 = headers
        .get("Last-Event-ID")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);

    let pool = state.db_pool.clone();
    let exec_id = id.clone();
    let mut rx = state.event_broadcast.subscribe();
    let mut last_sent_id = since_id;

    let stream = async_stream::stream! {
        // Backfill: send all events since since_id
        match db::events::list_by_execution_since(&pool, &exec_id, last_sent_id).await {
            Ok(events) => {
                for event in &events {
                    if let Some(sse_event) = sse_event_from_db_event(event) {
                        last_sent_id = event.id;
                        yield Ok::<Event, Infallible>(sse_event);
                    }
                }
                if has_terminal_event(&events) { return; }

                // If backfill returned no new events, the client may have already
                // received the terminal event (reconnect with Last-Event-ID past it).
                // Check execution status directly to avoid hanging forever.
                if events.is_empty()
                    && let Ok(exec) = db::executions::get_by_id(&pool, &exec_id).await
                    && matches!(exec.status.as_str(), "completed" | "failed" | "canceled")
                {
                    return;
                }
            }
            Err(e) => {
                tracing::error!("SSE backfill failed: {e}");
                return;
            }
        }

        // Live: wait for broadcast notifications
        loop {
            match rx.recv().await {
                Ok(notification) => {
                    if notification.execution_id != exec_id { continue; }

                    // Ephemeral events: yield directly as named SSE event, no DB query
                    if let Some(ref eph) = notification.ephemeral {
                        if let Ok(json) = serde_json::to_string(&serde_json::json!({
                            "session_id": eph.session_id,
                            "msg_seq": eph.msg_seq,
                            "payload": eph.payload,
                        })) {
                            yield Ok::<Event, Infallible>(
                                Event::default().event("ephemeral").data(json)
                            );
                        }
                        continue;
                    }

                    // Persisted events: query DB as before
                    match db::events::list_by_execution_since(&pool, &exec_id, last_sent_id).await {
                        Ok(events) => {
                            for event in &events {
                                if let Some(sse_event) = sse_event_from_db_event(event) {
                                    last_sent_id = event.id;
                                    yield Ok::<Event, Infallible>(sse_event);
                                }
                            }
                            if has_terminal_event(&events) { return; }
                        }
                        Err(e) => {
                            tracing::error!("SSE query failed: {e}");
                            return;
                        }
                    }
                }
                Err(broadcast::error::RecvError::Lagged(n)) => {
                    tracing::warn!(lagged = n, "SSE receiver lagged, backfilling from DB");
                    match db::events::list_by_execution_since(&pool, &exec_id, last_sent_id).await {
                        Ok(events) => {
                            for event in &events {
                                if let Some(sse_event) = sse_event_from_db_event(event) {
                                    last_sent_id = event.id;
                                    yield Ok::<Event, Infallible>(sse_event);
                                }
                            }
                            if has_terminal_event(&events) { return; }
                        }
                        Err(e) => {
                            tracing::error!("SSE backfill after lag failed: {e}");
                            return;
                        }
                    }
                }
                Err(broadcast::error::RecvError::Closed) => return,
            }
        }
    };

    let mut headers = HeaderMap::new();
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    headers.insert("x-accel-buffering", HeaderValue::from_static("no"));

    Ok((
        headers,
        Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))),
    ))
}

/// Convert a DB event into an SSE Event.
/// Uses `id:` for Last-Event-ID reconnection, `data:` contains full JSON.
/// No `event:` field — all events go through the default `onmessage` handler.
/// Returns None on serialization failure — caller should skip the event
/// rather than send empty data (which would advance Last-Event-ID and
/// permanently lose the event on reconnect).
fn sse_event_from_db_event(event: &db::events::Event) -> Option<Event> {
    let response = EventResponse::from(event.clone());
    match serde_json::to_string(&response) {
        Ok(json) => Some(Event::default().id(event.id.to_string()).data(json)),
        Err(e) => {
            tracing::warn!(event_id = event.id, error = %e, "failed to serialize SSE event, skipping");
            None
        }
    }
}

/// Check if any event in the batch is an execution-level terminal state_change.
/// Session-level terminal events (session_id is Some) are ignored — a child
/// session completing does not mean the execution is done.
fn has_terminal_event(events: &[db::events::Event]) -> bool {
    events.iter().any(|e| {
        e.session_id.is_none()
            && e.event_type == "state_change"
            && serde_json::from_str::<serde_json::Value>(&e.payload)
                .ok()
                .and_then(|v| v.get("to").and_then(|t| t.as_str()).map(String::from))
                .is_some_and(|to| matches!(to.as_str(), "completed" | "failed" | "canceled"))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_event(event_type: &str, payload: &str) -> db::events::Event {
        db::events::Event {
            id: 1,
            execution_id: "exec-1".into(),
            session_id: None,
            event_type: event_type.into(),
            payload: payload.into(),
            msg_seq: None,
            created_at: chrono::Utc::now(),
        }
    }

    #[test]
    fn test_has_terminal_event_completed() {
        let events = vec![make_event(
            "state_change",
            r#"{"from":"working","to":"completed"}"#,
        )];
        assert!(has_terminal_event(&events));
    }

    #[test]
    fn test_has_terminal_event_failed() {
        let events = vec![make_event(
            "state_change",
            r#"{"from":"working","to":"failed"}"#,
        )];
        assert!(has_terminal_event(&events));
    }

    #[test]
    fn test_has_terminal_event_canceled() {
        let events = vec![make_event(
            "state_change",
            r#"{"from":"working","to":"canceled"}"#,
        )];
        assert!(has_terminal_event(&events));
    }

    #[test]
    fn test_has_terminal_event_nonterminal() {
        let events = vec![make_event(
            "state_change",
            r#"{"from":"submitted","to":"working"}"#,
        )];
        assert!(!has_terminal_event(&events));
    }

    #[test]
    fn test_has_terminal_event_message_type_not_terminal() {
        let events = vec![make_event(
            "message",
            r#"{"role":"agent","parts":[{"kind":"text","text":"hi"}]}"#,
        )];
        assert!(!has_terminal_event(&events));
    }

    #[test]
    fn test_has_terminal_event_empty_batch() {
        assert!(!has_terminal_event(&[]));
    }

    #[test]
    fn test_session_level_terminal_event_does_not_close_stream() {
        // A child session completing should NOT terminate the execution SSE stream
        let events = vec![db::events::Event {
            session_id: Some("session-child-1".into()),
            ..make_event("state_change", r#"{"from":"working","to":"completed"}"#)
        }];
        assert!(!has_terminal_event(&events));
    }

    #[test]
    fn test_session_level_failed_does_not_close_stream() {
        let events = vec![db::events::Event {
            session_id: Some("session-child-1".into()),
            ..make_event("state_change", r#"{"from":"working","to":"failed"}"#)
        }];
        assert!(!has_terminal_event(&events));
    }
}
