//! Migration 0024 backfill: synthesizes a resolution marker for every eligible historical
//! answer source. Runs as a code-step inside the 0024 migration transaction.

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use sqlx::{Any, Row, Transaction};

use super::DbPool;
use crate::error::SchedulerError;
use crate::resolution;

const DEFAULT_MAP_CAP_MIB: u64 = 64;
const OFFLINE_CEILING_SECS: u64 = 600;
const PER_ENTRY_OVERHEAD: usize = 64;
const READ_CHUNK_ROWS: usize = 256;
const READ_CHUNK_BYTES: usize = 8 * 1024 * 1024;
const INSERT_CHUNK_ROWS: usize = 200;
const INSERT_CHUNK_BYTES: usize = 4 * 1024 * 1024;

fn flush_before_push(buf_len: usize, buf_bytes: usize, marker_len: usize) -> bool {
    buf_len > 0 && buf_bytes + marker_len > INSERT_CHUNK_BYTES
}

/// Summary of what the backfill did, for the migration log and tests.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct BackfillStats {
    pub sources_seen: usize,
    pub markers_synthesized: usize,
    pub already_covered: usize,
    pub envelope_oversize_skipped: usize,
}

pub(crate) fn map_cap_bytes() -> usize {
    let mib = std::env::var("AGENTBEACON_BACKFILL_MAP_CAP_MIB")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|v| *v > 0)
        .unwrap_or(DEFAULT_MAP_CAP_MIB);
    (mib as usize).saturating_mul(1024 * 1024)
}

fn extrapolate_total_bytes(
    retained: usize,
    completed_bytes: usize,
    rows_seen: i64,
    rows_total: i64,
) -> usize {
    if rows_seen <= 0 {
        return retained;
    }
    let current = retained.saturating_sub(completed_bytes) as f64;
    let rate = current / rows_seen as f64;
    (completed_bytes as f64 + rate * rows_total as f64).max(retained as f64) as usize
}

fn cap_abort(
    phase: &str,
    retained: usize,
    cap: usize,
    completed_bytes: usize,
    rows_seen: i64,
    rows_total: i64,
) -> SchedulerError {
    let mib = |b: usize| (b as f64) / (1024.0 * 1024.0);
    let est_total = extrapolate_total_bytes(retained, completed_bytes, rows_seen, rows_total);
    SchedulerError::Database(format!(
        "decisions backfill 0024: retained key bytes reached {:.1} MiB (cap {:.0} MiB) during the \
         {phase} phase ({rows_seen}/{rows_total} candidate rows scanned); est. total ~{:.1} MiB \
         (completed phases ~{:.1} MiB constant + current-phase rate extrapolated over its rows); \
         raise AGENTBEACON_BACKFILL_MAP_CAP_MIB above the estimate and retry — a database far \
         above the cap may need more than one increase",
        mib(retained),
        mib(cap),
        mib(est_total),
        mib(completed_bytes),
    ))
}

fn non_positive_id_abort(id: i64) -> SchedulerError {
    SchedulerError::Database(format!(
        "decisions backfill 0024: found an eligible event with a non-positive id ({id}); the \
         event-id sequence is corrupt and a marker's resolved_event_id could never validate — the \
         migration was rolled back (schema stays at 23). Repair the offending row(s) and retry"
    ))
}

fn unformattable_created_at_abort(id: i64) -> SchedulerError {
    SchedulerError::Database(format!(
        "decisions backfill 0024: eligible source event {id} has an unparseable created_at (the \
         timestamp formatter returned NULL or a value the RFC3339 read parser rejects); a marker's \
         resolved_at could not be formed. Repair the row's created_at and retry"
    ))
}

/// A retained per-source key: the batch owner identity plus the source message event id.
pub(crate) type SourceKey = (String, String, String, i64);

/// True once a backfill deadline (if any) has passed.
fn past_deadline(deadline: Option<Instant>) -> bool {
    deadline.is_some_and(|d| Instant::now() >= d)
}

/// Abort with the offline-ceiling diagnostic if the deadline has passed.
fn check_deadline(deadline: Option<Instant>) -> Result<(), SchedulerError> {
    if past_deadline(deadline) {
        return Err(deadline_abort());
    }
    Ok(())
}

fn deadline_abort() -> SchedulerError {
    SchedulerError::Database(format!(
        "decisions backfill 0024: exceeded the {}-minute offline/lock ceiling; the migration \
         was rolled back (schema stays at 23) — retry when the database can complete the \
         backfill within the window",
        OFFLINE_CEILING_SECS / 60
    ))
}

fn backfill_failpoint(site: &str) -> Result<(), SchedulerError> {
    if std::env::var("AGENTBEACON_TEST_FAIL_AT").as_deref() == Ok(site) {
        return Err(SchedulerError::Database(format!(
            "test failpoint at {site}"
        )));
    }
    Ok(())
}

/// Remaining offline budget in milliseconds; 0 once the deadline has passed.
fn remaining_budget_ms(deadline: Instant, now: Instant) -> i64 {
    deadline.saturating_duration_since(now).as_millis() as i64
}

async fn enforce_pg_timeout(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    deadline: Option<Instant>,
) -> Result<(), SchedulerError> {
    if !pool.is_postgres() {
        return Ok(());
    }
    if let Some(d) = deadline {
        let ms = remaining_budget_ms(d, Instant::now());
        if ms <= 0 {
            return Err(deadline_abort());
        }
        sqlx::query(&format!("SET LOCAL statement_timeout = {ms}"))
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("0024 set statement_timeout: {e}")))?;
    }
    Ok(())
}

/// The SQL byte-length expression for a payload column on the active backend.
fn payload_bytes_expr(pool: &DbPool, col: &str) -> String {
    if pool.is_postgres() {
        format!("octet_length({col})")
    } else {
        format!("length(CAST({col} AS BLOB))")
    }
}

/// Count of candidate rows matching a lane predicate; the denominator for the cap estimate.
async fn candidate_count(
    tx: &mut Transaction<'_, Any>,
    predicate: &str,
) -> Result<i64, SchedulerError> {
    let sql = format!("SELECT COUNT(*) AS c FROM events WHERE {predicate}");
    let row = sqlx::query(&sql)
        .fetch_one(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("0024 candidate count failed: {e}")))?;
    Ok(row.get::<i64, _>("c"))
}

/// Return every uncovered eligible answer source by key (shared by backfill and detector).
pub(crate) async fn collect_uncovered_sources(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    cap: usize,
    deadline: Option<Instant>,
    abort_on_non_positive_id: bool,
) -> Result<(Vec<SourceKey>, BackfillStats), SchedulerError> {
    use futures_util::TryStreamExt;

    let mut retained: usize = 0;
    let mut owner_map: HashMap<String, (String, String)> = HashMap::new();
    let mut existing: HashSet<SourceKey> = HashSet::new();

    check_deadline(deadline)?;
    enforce_pg_timeout(pool, tx, deadline).await?;
    let platform_total = candidate_count(tx, "event_type = 'platform'").await?;
    let coherence_expr = "CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END";
    let platform_sql = pool.prepare_query(&format!(
        "SELECT e.id AS id, e.execution_id AS execution_id, e.session_id AS session_id, \
         e.payload AS payload, {coherence_expr} AS coherent \
         FROM events e \
         LEFT JOIN sessions s ON s.id = e.session_id AND s.execution_id = e.execution_id \
         WHERE e.event_type = 'platform' ORDER BY e.id ASC"
    ));
    enforce_pg_timeout(pool, tx, deadline).await?;
    let mut platform_rows: i64 = 0;
    {
        let mut stream = sqlx::query(&platform_sql).fetch(&mut **tx);
        while let Some(row) = stream
            .try_next()
            .await
            .map_err(|e| SchedulerError::Database(format!("0024 platform scan failed: {e}")))?
        {
            check_deadline(deadline)?;
            platform_rows += 1;
            let id: i64 = row.get("id");
            let exec: String = row.get("execution_id");
            let session: Option<String> = row.get("session_id");
            let coherent = row
                .try_get::<bool, _>("coherent")
                .unwrap_or_else(|_| row.get::<i32, _>("coherent") != 0);
            let Ok(payload) = row.try_get::<String, _>("payload") else {
                continue;
            };

            if coherent && let Some(session) = &session {
                let escalates = resolution::escalate_parts(&payload);
                if abort_on_non_positive_id && !escalates.is_empty() && id <= 0 {
                    return Err(non_positive_id_abort(id));
                }
                for part in escalates {
                    if !owner_map.contains_key(&part.batch_id) {
                        retained +=
                            part.batch_id.len() + exec.len() + session.len() + PER_ENTRY_OVERHEAD;
                        if retained > cap {
                            return Err(cap_abort(
                                "platform",
                                retained,
                                cap,
                                0,
                                platform_rows,
                                platform_total,
                            ));
                        }
                        owner_map.insert(part.batch_id.clone(), (exec.clone(), session.clone()));
                    }
                }
            }

            if let Some(sess) = &session
                && let Some(parts) = resolution::parse_parts(&payload)
            {
                for cand in resolution::resolution_candidates(&parts, id) {
                    let Some(rid) = cand.embedded_resolved_event_id else {
                        continue;
                    };
                    let key = (exec.clone(), sess.clone(), cand.batch_id, rid);
                    if !existing.contains(&key) {
                        retained +=
                            key.0.len() + key.1.len() + key.2.len() + 8 + PER_ENTRY_OVERHEAD;
                        if retained > cap {
                            return Err(cap_abort(
                                "platform",
                                retained,
                                cap,
                                0,
                                platform_rows,
                                platform_total,
                            ));
                        }
                        existing.insert(key);
                    }
                }
            }
        }
    }
    let platform_retained = retained;

    check_deadline(deadline)?;
    enforce_pg_timeout(pool, tx, deadline).await?;
    let mut uncovered: Vec<SourceKey> = Vec::new();
    let mut stats = BackfillStats::default();
    let message_predicate = "event_type = 'message' AND payload LIKE '%question_answer%'";
    let message_total = candidate_count(tx, message_predicate).await?;
    let created_fmt = pool.format_timestamp(super::TimestampColumn::CreatedAt);
    let message_sql = pool.prepare_query(&format!(
        "SELECT id, payload, {created_fmt} AS created_at FROM events \
         WHERE {message_predicate} ORDER BY id ASC"
    ));
    enforce_pg_timeout(pool, tx, deadline).await?;
    let mut message_rows: i64 = 0;
    {
        let mut stream = sqlx::query(&message_sql).fetch(&mut **tx);
        while let Some(row) = stream
            .try_next()
            .await
            .map_err(|e| SchedulerError::Database(format!("0024 message scan failed: {e}")))?
        {
            check_deadline(deadline)?;
            message_rows += 1;
            let id: i64 = row.get("id");
            let Ok(payload) = row.try_get::<String, _>("payload") else {
                continue;
            };
            let Some((role, parts)) = resolution::parse_role_and_parts(&payload) else {
                continue;
            };
            let Some((batch_id, answer)) =
                resolution::historical_answer_source_ref(role.as_deref(), &parts)
            else {
                continue;
            };
            let Some((exec, session)) = owner_map.get(&batch_id) else {
                continue;
            };
            if abort_on_non_positive_id && id <= 0 {
                return Err(non_positive_id_abort(id));
            }
            stats.sources_seen += 1;
            if resolution::source_yields_no_marker(&batch_id, id, answer) {
                stats.envelope_oversize_skipped += 1;
                continue;
            }
            let key = (exec.clone(), session.clone(), batch_id, id);
            if existing.contains(&key) {
                stats.already_covered += 1;
                continue;
            }
            retained += exec.len() + session.len() + key.2.len() + 8 + PER_ENTRY_OVERHEAD;
            if retained > cap {
                return Err(cap_abort(
                    "message",
                    retained,
                    cap,
                    platform_retained,
                    message_rows,
                    message_total,
                ));
            }
            let created_at: Option<String> = row
                .try_get("created_at")
                .map_err(|e| SchedulerError::Database(format!("0024 read created_at: {e}")))?;
            if !created_at
                .as_deref()
                .is_some_and(resolution::resolved_at_is_representable)
            {
                return Err(unformattable_created_at_abort(id));
            }
            uncovered.push(key);
        }
    }

    Ok((uncovered, stats))
}

/// The offline/lock ceiling deadline for a single 0024 run.
pub(crate) fn offline_deadline() -> Instant {
    Instant::now() + Duration::from_secs(OFFLINE_CEILING_SECS)
}

/// Abort if the deadline has passed, else re-issue the remaining-budget statement_timeout (PG).
pub(crate) async fn enforce_deadline(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    deadline: Option<Instant>,
) -> Result<(), SchedulerError> {
    check_deadline(deadline)?;
    enforce_pg_timeout(pool, tx, deadline).await
}

/// Run the 0024 backfill on the held migration transaction.
pub async fn run_backfill_0024(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    deadline: Option<Instant>,
) -> Result<BackfillStats, SchedulerError> {
    let (mut uncovered, mut stats) =
        collect_uncovered_sources(pool, tx, map_cap_bytes(), deadline, true).await?;

    uncovered.sort_by_key(|k| k.3);
    let created_fmt = pool.format_timestamp(super::TimestampColumn::CreatedAt);
    let len_expr = payload_bytes_expr(pool, "payload");
    let mut insert_buf: Vec<(String, String, String)> = Vec::new();
    let mut insert_bytes = 0usize;
    let mut flushes = 0usize;

    let mut i = 0;
    while i < uncovered.len() {
        check_deadline(deadline)?;
        enforce_pg_timeout(pool, tx, deadline).await?;
        let window_end = (i + READ_CHUNK_ROWS).min(uncovered.len());
        let window_ids: Vec<i64> = uncovered[i..window_end].iter().map(|k| k.3).collect();
        let lens = plan_lengths(pool, tx, &len_expr, &window_ids).await?;
        let mut chunk_end = i;
        let mut chunk_bytes = 0usize;
        for k in &uncovered[i..window_end] {
            let l = *lens.get(&k.3).unwrap_or(&0);
            if chunk_end > i && chunk_bytes + l > READ_CHUNK_BYTES {
                break;
            }
            chunk_bytes += l;
            chunk_end += 1;
        }

        let chunk_ids: Vec<i64> = uncovered[i..chunk_end].iter().map(|k| k.3).collect();
        enforce_pg_timeout(pool, tx, deadline).await?;
        let rows = fetch_source_rows(pool, tx, &created_fmt, &chunk_ids).await?;
        for (exec, session, batch, source_id) in &uncovered[i..chunk_end] {
            check_deadline(deadline)?;
            let Some((payload, created_at)) = rows.get(source_id) else {
                continue;
            };
            debug_assert!(resolution::resolved_at_is_representable(created_at));
            let Some((role, parts)) = resolution::parse_role_and_parts(payload) else {
                continue;
            };
            let Some((_batch_id, answer)) =
                resolution::historical_answer_source_ref(role.as_deref(), &parts)
            else {
                continue;
            };
            let (text, truncated) = match answer {
                Some(t) => resolution::fit_answer_text(batch, *source_id, created_at, t),
                None => (None, false),
            };
            let marker = resolution::marker_payload(
                batch,
                *source_id,
                created_at,
                text.as_deref(),
                truncated,
            );
            let marker_str = serde_json::to_string(&marker)
                .map_err(|e| SchedulerError::Database(format!("0024 serialize marker: {e}")))?;
            if marker_str.len() > resolution::MARKER_SERIALIZED_CAP_BYTES {
                stats.envelope_oversize_skipped += 1;
                continue;
            }
            if flush_before_push(insert_buf.len(), insert_bytes, marker_str.len()) {
                check_deadline(deadline)?;
                enforce_pg_timeout(pool, tx, deadline).await?;
                flush_markers(pool, tx, &insert_buf).await?;
                insert_buf.clear();
                insert_bytes = 0;
                flushes += 1;
                if flushes == 1 {
                    backfill_failpoint("backfill_after_flush")?;
                }
            }
            insert_bytes += marker_str.len();
            insert_buf.push((exec.clone(), session.clone(), marker_str));
            stats.markers_synthesized += 1;
            if insert_buf.len() >= INSERT_CHUNK_ROWS || insert_bytes >= INSERT_CHUNK_BYTES {
                check_deadline(deadline)?;
                enforce_pg_timeout(pool, tx, deadline).await?;
                flush_markers(pool, tx, &insert_buf).await?;
                insert_buf.clear();
                insert_bytes = 0;
                flushes += 1;
                if flushes == 1 {
                    backfill_failpoint("backfill_after_flush")?;
                }
            }
        }
        i = chunk_end;
    }
    if !insert_buf.is_empty() {
        check_deadline(deadline)?;
        enforce_pg_timeout(pool, tx, deadline).await?;
        flush_markers(pool, tx, &insert_buf).await?;
        flushes += 1;
        if flushes == 1 {
            backfill_failpoint("backfill_after_flush")?;
        }
    }

    check_deadline(deadline)?;
    Ok(stats)
}

/// Byte length of each listed source payload (for read-chunk planning).
async fn plan_lengths(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    len_expr: &str,
    ids: &[i64],
) -> Result<HashMap<i64, usize>, SchedulerError> {
    if ids.is_empty() {
        return Ok(HashMap::new());
    }
    let placeholders = vec!["?"; ids.len()].join(", ");
    let sql = pool.prepare_query(&format!(
        "SELECT id, {len_expr} AS len FROM events WHERE id IN ({placeholders})"
    ));
    let mut q = sqlx::query(&sql);
    for id in ids {
        q = q.bind(id);
    }
    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("0024 length planning failed: {e}")))?;
    let mut out = HashMap::with_capacity(rows.len());
    for r in rows {
        let id: i64 = r.get("id");
        let len = r
            .try_get::<i64, _>("len")
            .unwrap_or_else(|_| r.get::<i32, _>("len") as i64);
        out.insert(id, len.max(0) as usize);
    }
    Ok(out)
}

/// Fetch the full payload + dialect-formatted created_at for a byte-bounded chunk of ids.
async fn fetch_source_rows(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    created_fmt: &str,
    ids: &[i64],
) -> Result<HashMap<i64, (String, String)>, SchedulerError> {
    if ids.is_empty() {
        return Ok(HashMap::new());
    }
    let placeholders = vec!["?"; ids.len()].join(", ");
    let sql = pool.prepare_query(&format!(
        "SELECT id, payload, {created_fmt} AS created_at FROM events WHERE id IN ({placeholders})"
    ));
    let mut q = sqlx::query(&sql);
    for id in ids {
        q = q.bind(id);
    }
    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("0024 source re-read failed: {e}")))?;
    let mut out = HashMap::with_capacity(rows.len());
    for r in rows {
        let id: i64 = r.get("id");
        let Ok(payload) = r.try_get::<String, _>("payload") else {
            continue;
        };
        let created_at: Option<String> = r
            .try_get("created_at")
            .map_err(|e| SchedulerError::Database(format!("0024 read created_at: {e}")))?;
        let Some(created_at) = created_at.filter(|s| resolution::resolved_at_is_representable(s))
        else {
            return Err(unformattable_created_at_abort(id));
        };
        out.insert(id, (payload, created_at));
    }
    Ok(out)
}

/// Insert a chunk of markers as platform events in a single multi-row statement.
async fn flush_markers(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
    markers: &[(String, String, String)],
) -> Result<(), SchedulerError> {
    if markers.is_empty() {
        return Ok(());
    }
    let values: Vec<&str> = markers.iter().map(|_| "(?, ?, 'platform', ?)").collect();
    let sql = pool.prepare_query(&format!(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES {}",
        values.join(", ")
    ));
    let mut q = sqlx::query(&sql);
    for (exec, session, payload) in markers {
        q = q.bind(exec).bind(session).bind(payload);
    }
    q.execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("0024 marker insert failed: {e}")))?;
    Ok(())
}
