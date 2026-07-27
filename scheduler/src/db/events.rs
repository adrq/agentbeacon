use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub id: i64,
    pub execution_id: String,
    pub session_id: Option<String>, // nullable for execution-level events
    pub event_type: String,         // "message" | "state_change"
    pub payload: String,            // JSON
    pub msg_seq: Option<i64>,       // monotonic per session, for dedup
    pub created_at: DateTime<Utc>,
}

pub async fn insert(
    pool: &DbPool,
    execution_id: &str,
    session_id: Option<&str>,
    event_type: &str,
    payload: &str,
) -> Result<i64, SchedulerError> {
    let sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, ?, ?) RETURNING id",
    );

    let row = sqlx::query(&sql)
        .bind(execution_id)
        .bind(session_id)
        .bind(event_type)
        .bind(payload)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert event failed: {e}")))?;

    let id: i64 = row
        .try_get("id")
        .map_err(|e| SchedulerError::Database(format!("get id failed: {e}")))?;
    Ok(id)
}

/// Insert with dedup on (session_id, msg_seq). Returns Some(id) if newly
/// inserted, None if a row with this session_id+msg_seq already exists.
pub async fn insert_with_dedup(
    pool: &DbPool,
    execution_id: &str,
    session_id: &str,
    event_type: &str,
    payload: &str,
    msg_seq: i64,
) -> Result<Option<i64>, SchedulerError> {
    let sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload, msg_seq) \
         VALUES (?, ?, ?, ?, ?) \
         ON CONFLICT (session_id, msg_seq) DO NOTHING \
         RETURNING id",
    );
    let row = sqlx::query(&sql)
        .bind(execution_id)
        .bind(session_id)
        .bind(event_type)
        .bind(payload)
        .bind(msg_seq)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert event dedup failed: {e}")))?;
    Ok(row.map(|r| r.get("id")))
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
        // An undecodable payload (invalid-UTF-8 BLOB) parse-skips, same as malformed JSON.
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
        // An undecodable payload (invalid-UTF-8 BLOB) parse-skips, same as malformed JSON.
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

    // created_at is qualified to the events alias so the sessions join cannot make it
    // ambiguous (sessions also has a created_at column).
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
        // A row with an undecodable payload parse-skips, same as malformed JSON.
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
// A corrupt-payload row parse-skips, same domain rule as malformed JSON, so one bad row never
// 500s a whole listing.
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
    let mut out = Vec::with_capacity(rows.len());
    for row in rows {
        if let Some(event) = parse_event_row(row)? {
            out.push(event);
        }
    }
    Ok(out)
}
