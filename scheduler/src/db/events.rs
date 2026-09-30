// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::api::problem::{Problem, ProblemCode};
use crate::db::executions::ExecutionTx;
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub id: i64,
    pub execution_id: String,
    pub session_id: Option<String>,
    pub event_type: String,
    pub payload: String,
    pub msg_seq: Option<i64>,
    pub created_at: DateTime<Utc>,
}

/// A freshly written event row: its id and the timestamp the database assigned.
#[derive(Debug, Clone)]
pub struct InsertedEvent {
    pub id: i64,
    /// RFC 3339 UTC.
    pub created_at: String,
}

/// Verify that `session_id` belongs to this transaction's execution.
///
async fn verify_session_owner(
    pool: &DbPool,
    tx: &mut ExecutionTx<'_>,
    session_id: &str,
) -> Result<(), SchedulerError> {
    if tx.session_verified(session_id) {
        return Ok(());
    }
    let sql = pool.prepare_query("SELECT execution_id FROM sessions WHERE id = ?");
    let row = sqlx::query(&sql)
        .bind(session_id)
        .fetch_optional(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("session owner lookup failed: {e}")))?;
    let owner: Option<String> = row.map(|r| r.get("execution_id"));
    if owner.as_deref() == Some(tx.execution_id()) {
        tx.mark_session_verified(session_id);
        return Ok(());
    }
    tracing::error!(
        session_id = %session_id,
        locked_execution_id = %tx.execution_id(),
        session_execution_id = ?owner,
        "rejected event write: session does not belong to the locked execution"
    );
    Err(SchedulerError::Problem(Box::new(
        Problem::new(ProblemCode::InternalError)
            .with_detail("event session does not belong to the locked execution"),
    )))
}

fn insert_returning_sql(pool: &DbPool, columns: &str) -> String {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    pool.prepare_query(&format!(
        "INSERT INTO events ({columns}) VALUES ({placeholders}) \
         RETURNING id, {created_fmt} AS created_at",
        placeholders = columns
            .split(',')
            .map(|_| "?")
            .collect::<Vec<_>>()
            .join(", ")
    ))
}

fn read_inserted(row: sqlx::any::AnyRow) -> Result<InsertedEvent, SchedulerError> {
    Ok(InsertedEvent {
        id: row
            .try_get("id")
            .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?,
        created_at: row.try_get("created_at").unwrap_or_default(),
    })
}

/// Insert an event inside an execution transaction.
///
/// `session_id` is `None` for execution-level rows.
pub async fn insert_in_tx(
    pool: &DbPool,
    tx: &mut ExecutionTx<'_>,
    session_id: Option<&str>,
    event_type: &str,
    payload: &str,
) -> Result<InsertedEvent, SchedulerError> {
    if let Some(session_id) = session_id {
        verify_session_owner(pool, tx, session_id).await?;
    }
    let sql = insert_returning_sql(pool, "execution_id, session_id, event_type, payload");
    let execution_id = tx.execution_id().to_string();
    let row = sqlx::query(&sql)
        .bind(&execution_id)
        .bind(session_id)
        .bind(event_type)
        .bind(payload)
        .fetch_one(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("insert event failed: {e}")))?;
    read_inserted(row)
}

/// Insert an event with dedup on `(session_id, msg_seq)`.
///
/// Returns `None` when a row with this pair already exists.
pub async fn insert_with_dedup_in_tx(
    pool: &DbPool,
    tx: &mut ExecutionTx<'_>,
    session_id: &str,
    event_type: &str,
    payload: &str,
    msg_seq: i64,
) -> Result<Option<InsertedEvent>, SchedulerError> {
    verify_session_owner(pool, tx, session_id).await?;
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = pool.prepare_query(&format!(
        "INSERT INTO events (execution_id, session_id, event_type, payload, msg_seq) \
         VALUES (?, ?, ?, ?, ?) \
         ON CONFLICT (session_id, msg_seq) DO NOTHING \
         RETURNING id, {created_fmt} AS created_at"
    ));
    let execution_id = tx.execution_id().to_string();
    let row = sqlx::query(&sql)
        .bind(&execution_id)
        .bind(session_id)
        .bind(event_type)
        .bind(payload)
        .bind(msg_seq)
        .fetch_optional(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("insert event dedup failed: {e}")))?;
    row.map(read_inserted).transpose()
}

/// Insert an event in its own transaction.
///
/// The caller must not hold another connection or transaction.
pub async fn insert_locked(
    pool: &DbPool,
    execution_id: &str,
    session_id: Option<&str>,
    event_type: &str,
    payload: &str,
) -> Result<InsertedEvent, SchedulerError> {
    let mut tx = crate::db::executions::begin_execution_tx(pool, execution_id).await?;
    let inserted = match insert_in_tx(pool, &mut tx, session_id, event_type, payload).await {
        Ok(inserted) => inserted,
        Err(e) => {
            let _ = tx.rollback().await;
            return Err(e);
        }
    };
    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit event tx failed: {e}")))?;
    Ok(inserted)
}

/// Seed an event from a test fixture.
pub async fn insert_for_test(
    pool: &DbPool,
    execution_id: &str,
    session_id: Option<&str>,
    event_type: &str,
    payload: &str,
) -> Result<i64, SchedulerError> {
    insert_locked(pool, execution_id, session_id, event_type, payload)
        .await
        .map(|inserted| inserted.id)
}

pub async fn list_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, execution_id, session_id, event_type, payload, msg_seq, {} as created_at FROM events WHERE execution_id = ? ORDER BY id ASC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events failed: {e}")))?;

    collect_events(rows)
}

pub async fn list_by_session(
    pool: &DbPool,
    session_id: &str,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, execution_id, session_id, event_type, payload, msg_seq, {} as created_at FROM events WHERE session_id = ? ORDER BY id ASC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(session_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events failed: {e}")))?;

    collect_events(rows)
}

pub async fn list_by_execution_since(
    pool: &DbPool,
    execution_id: &str,
    since_id: i64,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, execution_id, session_id, event_type, payload, msg_seq, {} as created_at \
         FROM events WHERE execution_id = ? AND id > ? ORDER BY id ASC",
        created_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .bind(since_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events since failed: {e}")))?;

    collect_events(rows)
}

const EVENT_COLUMNS: &str = "id, execution_id, session_id, event_type, payload, msg_seq";

fn window_sql(pool: &DbPool, scope_column: &str, descending: bool) -> String {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let (comparison, order) = if descending {
        ("<", "DESC")
    } else {
        (">", "ASC")
    };
    pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events \
         WHERE {scope_column} = ? AND id {comparison} ? ORDER BY id {order} LIMIT ?"
    ))
}

fn newest_sql(pool: &DbPool, scope_column: &str) -> String {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events \
         WHERE {scope_column} = ? ORDER BY id DESC LIMIT ?"
    ))
}

async fn fetch_window(
    pool: &DbPool,
    sql: &str,
    scope: &str,
    cursor: Option<i64>,
    limit: i64,
) -> Result<EventScan, SchedulerError> {
    let mut query = sqlx::query(sql).bind(scope);
    if let Some(cursor) = cursor {
        query = query.bind(cursor);
    }
    let rows = query
        .bind(limit)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list events window failed: {e}")))?;
    collect_scan(rows)
}

/// The `limit` events immediately below `before`, ascending by id.
///
/// `before` is exclusive; `None` selects the newest events.
pub async fn list_by_execution_before(
    pool: &DbPool,
    execution_id: &str,
    before: Option<i64>,
    limit: i64,
) -> Result<EventScan, SchedulerError> {
    let sql = match before {
        Some(_) => window_sql(pool, "execution_id", true),
        None => newest_sql(pool, "execution_id"),
    };
    Ok(fetch_window(pool, &sql, execution_id, before, limit)
        .await?
        .reversed())
}

/// The `limit` events immediately above `after`, ascending by id. `after` is exclusive.
pub async fn list_by_execution_after(
    pool: &DbPool,
    execution_id: &str,
    after: i64,
    limit: i64,
) -> Result<EventScan, SchedulerError> {
    let sql = window_sql(pool, "execution_id", false);
    fetch_window(pool, &sql, execution_id, Some(after), limit).await
}

/// The `limit` events immediately below `before` in one session, ascending by id.
pub async fn list_by_session_before(
    pool: &DbPool,
    session_id: &str,
    before: Option<i64>,
    limit: i64,
) -> Result<EventScan, SchedulerError> {
    let sql = match before {
        Some(_) => window_sql(pool, "session_id", true),
        None => newest_sql(pool, "session_id"),
    };
    Ok(fetch_window(pool, &sql, session_id, before, limit)
        .await?
        .reversed())
}

/// The `limit` events immediately above `after` in one session, ascending by id.
pub async fn list_by_session_after(
    pool: &DbPool,
    session_id: &str,
    after: i64,
    limit: i64,
) -> Result<EventScan, SchedulerError> {
    let sql = window_sql(pool, "session_id", false);
    fetch_window(pool, &sql, session_id, Some(after), limit).await
}

/// Fetch one event by id.
pub async fn get_by_id(pool: &DbPool, id: i64) -> Result<Option<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events WHERE id = ?"
    ));
    let row = sqlx::query(&sql)
        .bind(id)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("get event failed: {e}")))?;
    match row {
        Some(row) => parse_event_row(row),
        None => Ok(None),
    }
}

/// The highest event id recorded for an execution.
pub async fn max_id_for_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Option<i64>, SchedulerError> {
    let sql = pool.prepare_query("SELECT MAX(id) as max_id FROM events WHERE execution_id = ?");
    let row = sqlx::query(&sql)
        .bind(execution_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("max event id failed: {e}")))?;
    Ok(row.try_get::<Option<i64>, _>("max_id").unwrap_or(None))
}

/// Platform-lane events of one execution with an id above `after_id`, ascending.
pub async fn list_platform_after_in_execution(
    pool: &DbPool,
    execution_id: &str,
    after_id: i64,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events \
         WHERE execution_id = ? AND event_type = 'platform' AND id > ? ORDER BY id ASC"
    ));
    let rows = sqlx::query(&sql)
        .bind(execution_id)
        .bind(after_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list platform tail failed: {e}")))?;
    collect_events(rows)
}

/// A chunk of the platform lane below `from_id`, newest first.
///
/// `from_id` is exclusive; `None` starts at the newest platform row.
pub async fn scan_platform_descending(
    pool: &DbPool,
    from_id: Option<i64>,
    limit: i64,
) -> Result<EventScan, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let predicate = match from_id {
        Some(_) => "AND id < ?",
        None => "",
    };
    let sql = pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events \
         WHERE event_type = 'platform' {predicate} ORDER BY id DESC LIMIT ?"
    ));
    let mut query = sqlx::query(&sql);
    if let Some(from_id) = from_id {
        query = query.bind(from_id);
    }
    let rows = query
        .bind(limit)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("scan platform lane failed: {e}")))?;
    collect_scan(rows)
}

/// Whether any platform-lane row exists strictly below `id`.
pub async fn platform_rows_exist_below(pool: &DbPool, id: i64) -> Result<bool, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT id FROM events WHERE event_type = 'platform' AND id < ? ORDER BY id DESC LIMIT 1",
    );
    let row = sqlx::query(&sql)
        .bind(id)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("platform tail probe failed: {e}")))?;
    Ok(row.is_some())
}

/// List message events for a session, optionally filtered by event ID.
pub async fn list_messages_by_session(
    pool: &DbPool,
    session_id: &str,
    since_id: Option<i64>,
) -> Result<Vec<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = if since_id.is_some() {
        format!(
            "SELECT id, execution_id, session_id, event_type, payload, msg_seq, {} as created_at \
             FROM events WHERE session_id = ? AND event_type = 'message' AND id > ? ORDER BY id ASC",
            created_fmt
        )
    } else {
        format!(
            "SELECT id, execution_id, session_id, event_type, payload, msg_seq, {} as created_at \
             FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY id ASC",
            created_fmt
        )
    };

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared).bind(session_id);
    if let Some(sid) = since_id {
        q = q.bind(sid);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list messages by session failed: {e}")))?;

    collect_events(rows)
}

/// An escalation event, resolved by its primary key.
#[derive(Debug, Clone)]
pub struct Escalation {
    pub id: i64,
    pub execution_id: String,
    pub session_id: String,
    pub created_at: DateTime<Utc>,
    pub data: crate::resolution::EscalationData,
}

fn as_escalation(event: Event) -> Option<Escalation> {
    if event.event_type != "platform" {
        return None;
    }
    let session_id = event.session_id.clone()?;
    let data = crate::resolution::escalation_data(&event.payload)?;
    Some(Escalation {
        id: event.id,
        execution_id: event.execution_id,
        session_id,
        created_at: event.created_at,
        data,
    })
}

/// Fetch an escalation by its event id.
///
/// Returns `None` when no such event exists or the event is not an escalation.
pub async fn get_escalation(
    pool: &DbPool,
    event_id: i64,
) -> Result<Option<Escalation>, SchedulerError> {
    Ok(get_by_id(pool, event_id).await?.and_then(as_escalation))
}

/// Fetch one event by id inside a held transaction.
pub async fn get_by_id_in_tx(
    pool: &DbPool,
    tx: &mut ExecutionTx<'_>,
    id: i64,
) -> Result<Option<Event>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events WHERE id = ?"
    ));
    let row = sqlx::query(&sql)
        .bind(id)
        .fetch_optional(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("get event failed: {e}")))?;
    match row {
        Some(row) => parse_event_row(row),
        None => Ok(None),
    }
}

/// Fetch an escalation by its event id inside a held transaction.
pub async fn get_escalation_in_tx(
    pool: &DbPool,
    tx: &mut ExecutionTx<'_>,
    event_id: i64,
) -> Result<Option<Escalation>, SchedulerError> {
    Ok(get_by_id_in_tx(pool, tx, event_id)
        .await?
        .and_then(as_escalation))
}

fn best_marker(
    events: Vec<Event>,
    escalation: &Escalation,
) -> Option<crate::resolution::ResolutionCandidate> {
    let mut best: Option<crate::resolution::ResolutionCandidate> = None;
    for event in events {
        if event.session_id.as_deref() != Some(escalation.session_id.as_str()) {
            continue;
        }
        let Some(parts) = crate::resolution::parse_parts(&event.payload) else {
            continue;
        };
        for mut cand in crate::resolution::resolution_candidates(&parts, event.id) {
            if cand.escalation_event_id != Some(escalation.id) {
                continue;
            }
            if cand.kind == crate::resolution::ResolutionKind::Dismiss {
                cand.resolved_at_raw = Some(
                    event
                        .created_at
                        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
                );
            }
            if best.as_ref().is_none_or(|b| cand.order_key < b.order_key) {
                best = Some(cand);
            }
        }
    }
    best
}

/// Find the resolution marker attached to an escalation, if any.
pub async fn find_marker_for_escalation(
    pool: &DbPool,
    escalation: &Escalation,
) -> Result<Option<crate::resolution::ResolutionCandidate>, SchedulerError> {
    let events =
        list_platform_after_in_execution(pool, &escalation.execution_id, escalation.id).await?;
    Ok(best_marker(events, escalation))
}

/// Find the resolution marker attached to an escalation inside a held transaction.
pub async fn find_marker_for_escalation_in_tx(
    pool: &DbPool,
    tx: &mut ExecutionTx<'_>,
    escalation: &Escalation,
) -> Result<Option<crate::resolution::ResolutionCandidate>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = pool.prepare_query(&format!(
        "SELECT {EVENT_COLUMNS}, {created_fmt} as created_at FROM events \
         WHERE execution_id = ? AND event_type = 'platform' AND id > ? ORDER BY id ASC"
    ));
    let rows = sqlx::query(&sql)
        .bind(&escalation.execution_id)
        .bind(escalation.id)
        .fetch_all(&mut ***tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("marker association read failed: {e}")))?;
    Ok(best_marker(collect_events(rows)?, escalation))
}

/// The execution and session that own a batch.
pub struct BatchOwner {
    pub execution_id: String,
    pub session_id: String,
}

/// Resolve the execution and session that own a batch (LIKE-prefiltered, parse-verified).
/// Streams the prefiltered rows and returns the first parse-verified owner.
pub async fn find_batch_owner(
    pool: &DbPool,
    batch_id: &str,
) -> Result<Option<BatchOwner>, SchedulerError> {
    use futures_util::TryStreamExt;

    let pattern = crate::resolution::like_pattern_for_batch_id(batch_id);
    let sql = pool.prepare_query(
        "SELECT e.execution_id AS execution_id, e.session_id AS session_id, e.payload AS payload \
         FROM events e JOIN sessions s ON s.id = e.session_id AND s.execution_id = e.execution_id \
         WHERE e.event_type = 'platform' AND e.payload LIKE ? ESCAPE '\\' ORDER BY e.id ASC",
    );
    let mut stream = sqlx::query(&sql).bind(&pattern).fetch(pool.as_ref());
    while let Some(row) = stream
        .try_next()
        .await
        .map_err(|e| SchedulerError::Database(format!("find_batch_owner failed: {e}")))?
    {
        let execution_id: String = row.get("execution_id");
        let session_id: Option<String> = row.get("session_id");
        let Some(session_id) = session_id else {
            continue;
        };
        let Ok(payload) = row.try_get::<String, _>("payload") else {
            continue;
        };
        if crate::resolution::escalate_parts(&payload)
            .iter()
            .any(|p| p.batch_id == batch_id)
        {
            return Ok(Some(BatchOwner {
                execution_id,
                session_id,
            }));
        }
    }
    Ok(None)
}

/// The winning resolution (answer or dismiss) for a batch.
pub struct ResolutionOutcome {
    pub kind: crate::resolution::ResolutionKind,
}

/// Find the resolution event for a batch within a held transaction.
pub async fn find_resolution_for_batch_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    owner_execution_id: &str,
    owner_session_id: &str,
    batch_id: &str,
) -> Result<Option<ResolutionOutcome>, SchedulerError> {
    let pattern = crate::resolution::like_pattern_for_batch_id(batch_id);
    let sql = pool.prepare_query(
        "SELECT id, session_id, payload FROM events \
         WHERE execution_id = ? AND event_type = 'platform' AND payload LIKE ? ESCAPE '\\' \
         ORDER BY id ASC",
    );
    let rows = sqlx::query(&sql)
        .bind(owner_execution_id)
        .bind(&pattern)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("find_resolution_for_batch_in_tx failed: {e}"))
        })?;

    let mut best: Option<(
        crate::resolution::OrderKey,
        crate::resolution::ResolutionKind,
    )> = None;
    for row in rows {
        let row_session: Option<String> = row.get("session_id");
        if row_session.as_deref() != Some(owner_session_id) {
            continue;
        }
        let id: i64 = row.get("id");
        let Ok(payload) = row.try_get::<String, _>("payload") else {
            continue;
        };
        let Some(parts) = crate::resolution::parse_parts(&payload) else {
            continue;
        };
        for cand in crate::resolution::resolution_candidates(&parts, id) {
            if cand.batch_id != batch_id {
                continue;
            }
            if best.as_ref().is_none_or(|(bk, _)| cand.order_key < *bk) {
                best = Some((cand.order_key, cand.kind));
            }
        }
    }
    Ok(best.map(|(_, kind)| ResolutionOutcome { kind }))
}

/// A windowed platform event with its session-coherence flag projected inline.
pub struct WindowEvent {
    pub event: Event,
    /// True if the event's session is non-null and belongs to the event's execution.
    pub session_coherent: bool,
}

/// List platform events for the given executions, each with its session-coherence flag.
pub async fn list_platform_events_for_executions(
    pool: &DbPool,
    execution_ids: &[String],
) -> Result<Vec<WindowEvent>, SchedulerError> {
    if execution_ids.is_empty() {
        return Ok(vec![]);
    }

    let created_expr = if pool.is_postgres() {
        "to_char(e.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
    } else {
        "strftime('%Y-%m-%dT%H:%M:%SZ', e.created_at)"
    };
    let placeholders: Vec<&str> = execution_ids.iter().map(|_| "?").collect();
    let in_clause = placeholders.join(", ");
    let sql = format!(
        "SELECT e.id AS id, e.execution_id AS execution_id, e.session_id AS session_id, \
         e.event_type AS event_type, e.payload AS payload, e.msg_seq AS msg_seq, \
         {created_expr} AS created_at, \
         CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END AS session_coherent \
         FROM events e \
         LEFT JOIN sessions s ON s.id = e.session_id AND s.execution_id = e.execution_id \
         WHERE e.execution_id IN ({in_clause}) AND e.event_type = 'platform' \
         ORDER BY e.id ASC"
    );
    let prepared = pool.prepare_query(&sql);

    let mut q = sqlx::query(&prepared);
    for id in execution_ids {
        q = q.bind(id);
    }

    let rows = q.fetch_all(pool.as_ref()).await.map_err(|e| {
        SchedulerError::Database(format!("list_platform_events_for_executions failed: {e}"))
    })?;
    let mut out = Vec::with_capacity(rows.len());
    for row in rows {
        let session_coherent = row
            .try_get::<bool, _>("session_coherent")
            .unwrap_or_else(|_| row.get::<i32, _>("session_coherent") != 0);
        if let Some(event) = parse_event_row(row)? {
            out.push(WindowEvent {
                event,
                session_coherent,
            });
        }
    }
    Ok(out)
}

/// Parse one event row, or None when its payload is undecodable (an invalid-UTF-8 BLOB).
fn parse_event_row(row: sqlx::any::AnyRow) -> Result<Option<Event>, SchedulerError> {
    let Ok(payload) = row.try_get::<String, _>("payload") else {
        return Ok(None);
    };
    Ok(Some(Event {
        id: row.get("id"),
        execution_id: row.get("execution_id"),
        session_id: row.get("session_id"),
        event_type: row.get("event_type"),
        payload,
        msg_seq: row.get("msg_seq"),
        created_at: parse_timestamp(&row, "created_at")?,
    }))
}

/// Parse a set of event rows, skipping any with an undecodable payload.
fn collect_events(rows: Vec<sqlx::any::AnyRow>) -> Result<Vec<Event>, SchedulerError> {
    Ok(collect_scan(rows)?.events)
}

/// A scanned window: the rows that decoded, and the ids of every row scanned.
pub struct EventScan {
    pub events: Vec<Event>,
    /// Every scanned id, in the order the query returned them.
    pub ids: Vec<i64>,
}

impl EventScan {
    /// Rows the query returned, decoded or not.
    pub fn scanned(&self) -> usize {
        self.ids.len()
    }

    /// The lowest scanned id, decoded or not.
    pub fn lowest_id(&self) -> Option<i64> {
        self.ids.iter().copied().min()
    }

    /// The highest scanned id, decoded or not.
    pub fn highest_id(&self) -> Option<i64> {
        self.ids.iter().copied().max()
    }

    /// Reverse both the decoded rows and the scanned ids.
    fn reversed(mut self) -> Self {
        self.events.reverse();
        self.ids.reverse();
        self
    }
}

fn collect_scan(rows: Vec<sqlx::any::AnyRow>) -> Result<EventScan, SchedulerError> {
    let mut events = Vec::with_capacity(rows.len());
    let mut ids = Vec::with_capacity(rows.len());
    for row in rows {
        ids.push(row.get("id"));
        if let Some(event) = parse_event_row(row)? {
            events.push(event);
        }
    }
    Ok(EventScan { events, ids })
}
