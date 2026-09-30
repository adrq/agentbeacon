// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use std::convert::Infallible;
use std::time::Duration;

use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::routing::get;
use axum::{Router, response::IntoResponse};
use tokio::sync::broadcast;

use crate::api::problem::{Problem, ProblemCode};
use crate::api::types::EventV1;
use crate::api::versions::EVENTS_MAX_PAGE;
use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

/// Catch-up interval for the event stream.
const CATCH_UP_INTERVAL: Duration = Duration::from_secs(15);

/// SSE route: `GET /api/v1/executions/{id}/events/stream`
pub fn routes() -> Router<AppState> {
    Router::new().route(
        "/executions/{id}/events/stream",
        get(execution_event_stream),
    )
}

async fn execution_event_stream(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::executions::get_by_id(&state.db_pool, &id)
        .await
        .map_err(|e| match e {
            SchedulerError::NotFound(_) => SchedulerError::Problem(Box::new(
                Problem::new(ProblemCode::ResourceNotFound).with_detail("no execution has this id"),
            )),
            other => other,
        })?;

    let pool = state.db_pool.clone();
    let exec_id = id.clone();

    let mut rx = state.event_broadcast.subscribe();

    let position = db::events::max_id_for_execution(&pool, &exec_id).await?;
    let mut last_sent_id = position.unwrap_or(0);

    let stream = async_stream::stream! {
        let history_before = position.and_then(|id| id.checked_add(1));
        let position_payload = serde_json::json!({
            "position": position.map(|id| id.to_string()),
            "history_before": history_before.map(|id| id.to_string()),
        });
        yield Ok::<Event, Infallible>(
            Event::default()
                .event("position")
                .data(position_payload.to_string()),
        );

        let mut interval = tokio::time::interval(CATCH_UP_INTERVAL);
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        interval.tick().await;

        loop {
            let mut ephemeral = None;
            tokio::select! {
                received = rx.recv() => match received {
                    Ok(notification) => {
                        if notification.execution_id != exec_id { continue; }
                        ephemeral = notification.ephemeral;
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        tracing::warn!(lagged = n, execution_id = %exec_id, "SSE receiver lagged, closing");
                        return;
                    }
                    Err(broadcast::error::RecvError::Closed) => return,
                },
                _ = interval.tick() => {}
            }

            if let Some(eph) = ephemeral
                && let Ok(json) = serde_json::to_string(&serde_json::json!({
                    "session_id": eph.session_id,
                    "msg_seq": eph.msg_seq,
                    "payload": eph.payload,
                }))
            {
                yield Ok::<Event, Infallible>(
                    Event::default().event("ephemeral").data(json)
                );
            }

            loop {
                let batch = db::events::list_by_execution_after(
                    &pool, &exec_id, last_sent_id, EVENTS_MAX_PAGE,
                ).await;
                let batch = match batch {
                    Ok(batch) => batch,
                    Err(e) => {
                        tracing::error!(execution_id = %exec_id, error = %e, "SSE read failed");
                        let problem = Problem::new(ProblemCode::InternalError)
                            .with_detail("the event stream could not be read");
                        yield Ok::<Event, Infallible>(
                            Event::default()
                                .event("protocol_error")
                                .data(problem.to_value().to_string()),
                        );
                        return;
                    }
                };
                let short_read = (batch.scanned() as i64) < EVENTS_MAX_PAGE;
                let highest_scanned = batch.highest_id();
                for event in &batch.events {
                    let terminal = is_terminal_event(event);
                    if let Some(sse_event) = sse_event_from_db_event(event) {
                        last_sent_id = event.id;
                        yield Ok::<Event, Infallible>(sse_event);
                    } else {
                        last_sent_id = event.id;
                    }
                    if terminal { return; }
                }
                if let Some(highest) = highest_scanned {
                    last_sent_id = last_sent_id.max(highest);
                }
                if short_read { break; }
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

/// Convert a DB event into an SSE Event carrying the same record REST serves.
fn sse_event_from_db_event(event: &db::events::Event) -> Option<Event> {
    let record = EventV1::from(event.clone());
    match serde_json::to_string(&record) {
        Ok(json) => Some(Event::default().id(event.id.to_string()).data(json)),
        Err(e) => {
            tracing::warn!(event_id = event.id, error = %e, "failed to serialize SSE event, skipping");
            None
        }
    }
}

/// Check if an event is an execution-level terminal state_change.
/// Session-level terminal events (session_id is Some) are ignored — a child
/// session completing does not mean the execution is done.
fn is_terminal_event(event: &db::events::Event) -> bool {
    event.session_id.is_none()
        && event.event_type == "state_change"
        && serde_json::from_str::<serde_json::Value>(&event.payload)
            .ok()
            .is_some_and(|v| {
                let outcome = v.get("outcome").and_then(|t| t.as_str());
                let to = v.get("to").and_then(|t| t.as_str());
                let terminal = outcome.or(to);
                matches!(terminal, Some("completed" | "failed" | "canceled"))
            })
}
