use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_optional_timestamp, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub agent_session_id: Option<String>,
    pub cwd: Option<String>,
    pub worktree_path: Option<String>,
    pub base_commit_sha: Option<String>,
    pub slug: String,
    pub recovery_attempts: i64,
    pub metadata: String,
    pub desired: String,
    pub executor_state: String,
    pub outcome: Option<String>,
    pub desired_by: Option<String>,
    pub desired_at: Option<DateTime<Utc>>,
    pub command_token: Option<String>,
    pub command_type: Option<String>,
    pub command_at: Option<DateTime<Utc>>,
    pub command_has_payload: bool,
    pub worker_id: Option<String>,
    pub parent_notified: bool,
    pub continued_from_session_id: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

/// Build the SELECT column list for session queries.
/// Timestamps need database-specific formatting via format_timestamp().
fn session_columns(pool: &DbPool) -> String {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);
    let desired_at_fmt = pool.format_timestamp(TimestampColumn::DesiredAt);
    let command_at_fmt = pool.format_timestamp(TimestampColumn::CommandAt);

    format!(
        "id, execution_id, parent_session_id, agent_id, agent_session_id, \
         cwd, worktree_path, base_commit_sha, slug, recovery_attempts, metadata, \
         desired, executor_state, outcome, desired_by, \
         {desired_at_fmt} as desired_at, \
         command_token, command_type, \
         {command_at_fmt} as command_at, \
         command_has_payload, worker_id, parent_notified, continued_from_session_id, \
         {created_fmt} as created_at, {updated_fmt} as updated_at, \
         {completed_fmt} as completed_at"
    )
}

/// Build the SELECT column list for session queries with table alias prefix.
fn session_columns_prefixed(pool: &DbPool, alias: &str) -> String {
    let created_fmt = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", &format!("{alias}.created_at"));
    let updated_fmt = pool
        .format_timestamp(TimestampColumn::UpdatedAt)
        .replace("updated_at", &format!("{alias}.updated_at"));
    let completed_fmt = pool
        .format_timestamp(TimestampColumn::CompletedAt)
        .replace("completed_at", &format!("{alias}.completed_at"));
    let desired_at_fmt = pool
        .format_timestamp(TimestampColumn::DesiredAt)
        .replace("desired_at", &format!("{alias}.desired_at"));
    let command_at_fmt = pool
        .format_timestamp(TimestampColumn::CommandAt)
        .replace("command_at", &format!("{alias}.command_at"));

    format!(
        "{alias}.id, {alias}.execution_id, {alias}.parent_session_id, {alias}.agent_id, \
         {alias}.agent_session_id, {alias}.cwd, {alias}.worktree_path, {alias}.base_commit_sha, \
         {alias}.slug, {alias}.recovery_attempts, {alias}.metadata, \
         {alias}.desired, {alias}.executor_state, {alias}.outcome, {alias}.desired_by, \
         {desired_at_fmt} as desired_at, \
         {alias}.command_token, {alias}.command_type, \
         {command_at_fmt} as command_at, \
         {alias}.command_has_payload, {alias}.worker_id, {alias}.parent_notified, \
         {alias}.continued_from_session_id, \
         {created_fmt} as created_at, {updated_fmt} as updated_at, \
         {completed_fmt} as completed_at"
    )
}

#[allow(clippy::too_many_arguments)]
pub async fn create(
    pool: &DbPool,
    id: &str,
    execution_id: &str,
    agent_id: &str,
    parent_session_id: Option<&str>,
    cwd: Option<&str>,
    worktree_path: Option<&str>,
    base_commit_sha: Option<&str>,
    slug: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, \
         worktree_path, base_commit_sha, slug) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(execution_id)
        .bind(parent_session_id)
        .bind(agent_id)
        .bind(cwd)
        .bind(worktree_path)
        .bind(base_commit_sha)
        .bind(slug)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create session failed: {e}")))?;

    Ok(())
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Session, SchedulerError> {
    let cols = session_columns(pool);
    let sql = format!("SELECT {cols} FROM sessions WHERE id = ?");
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("session", id, e))?;

    parse_session_row(row)
}

pub async fn list_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns(pool);
    let sql = format!("SELECT {cols} FROM sessions WHERE execution_id = ? ORDER BY created_at ASC");

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

/// List sessions for an execution with pending_turns counts.
pub async fn list_by_execution_with_pending(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<(Session, i64)>, SchedulerError> {
    let cols = session_columns(pool);
    let sql = format!(
        "SELECT {cols}, COALESCE(tq.cnt, 0) as pending_turns \
         FROM sessions \
         LEFT JOIN ( \
           SELECT session_id, COUNT(*) as cnt FROM task_queue \
           WHERE execution_id = ? GROUP BY session_id \
         ) tq ON tq.session_id = sessions.id \
         WHERE sessions.execution_id = ? \
         ORDER BY sessions.created_at ASC"
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions with pending failed: {e}")))?;

    rows.into_iter()
        .map(|row| {
            let pending: i64 = row.get("pending_turns");
            let session = parse_session_row(row)?;
            Ok((session, pending))
        })
        .collect()
}

/// Atomically create a child session only if the parent has fewer than
/// `max_width` active (non-terminal) children. Returns `true` if the
/// session was created, `false` if width limit was reached.
#[allow(clippy::too_many_arguments)]
pub async fn create_with_width_guard(
    pool: &DbPool,
    id: &str,
    execution_id: &str,
    agent_id: &str,
    parent_session_id: &str,
    cwd: Option<&str>,
    worktree_path: Option<&str>,
    max_width: i64,
    slug: &str,
) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, \
         worktree_path, slug) \
         SELECT ?, ?, ?, ?, ?, ?, ? \
         WHERE (SELECT COUNT(*) FROM sessions \
                WHERE parent_session_id = ? AND outcome IS NULL) < ?",
    );

    let result = sqlx::query(&query)
        .bind(id)
        .bind(execution_id)
        .bind(parent_session_id)
        .bind(agent_id)
        .bind(cwd)
        .bind(worktree_path)
        .bind(slug)
        .bind(parent_session_id)
        .bind(max_width)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create session failed: {e}")))?;

    Ok(result.rows_affected() > 0)
}

/// Count active (non-terminal) children of a session
pub async fn count_active_children(
    pool: &DbPool,
    parent_session_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM sessions WHERE parent_session_id = ? AND outcome IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(parent_session_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count active children failed: {e}")))?;

    Ok(row.get::<i64, _>("cnt"))
}

/// Compute session depth by walking the parent chain via recursive CTE.
/// Constrained to execution_id to prevent cross-execution traversal.
pub async fn compute_depth(
    pool: &DbPool,
    session_id: &str,
    execution_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "WITH RECURSIVE ancestors(id, parent_session_id, lvl) AS (\
            SELECT id, parent_session_id, 0 \
            FROM sessions WHERE id = ? AND execution_id = ? \
            UNION ALL \
            SELECT s.id, s.parent_session_id, a.lvl + 1 \
            FROM sessions s JOIN ancestors a ON s.id = a.parent_session_id \
            WHERE s.execution_id = ? AND a.lvl < 11 \
        ) \
        SELECT COALESCE(MAX(lvl), 0) as depth FROM ancestors",
    );

    let row = sqlx::query(&query)
        .bind(session_id)
        .bind(execution_id)
        .bind(execution_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("compute depth failed: {e}")))?;

    Ok(row.get::<i64, _>("depth"))
}

/// Like `compute_depth` but reads through an existing transaction.
pub async fn compute_depth_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    session_id: &str,
    execution_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "WITH RECURSIVE ancestors(id, parent_session_id, lvl) AS (\
            SELECT id, parent_session_id, 0 \
            FROM sessions WHERE id = ? AND execution_id = ? \
            UNION ALL \
            SELECT s.id, s.parent_session_id, a.lvl + 1 \
            FROM sessions s JOIN ancestors a ON s.id = a.parent_session_id \
            WHERE s.execution_id = ? AND a.lvl < 11 \
        ) \
        SELECT COALESCE(MAX(lvl), 0) as depth FROM ancestors",
    );

    let row = sqlx::query(&query)
        .bind(session_id)
        .bind(execution_id)
        .bind(execution_id)
        .fetch_one(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("compute depth failed: {e}")))?;

    Ok(row.get::<i64, _>("depth"))
}

pub async fn list_filtered(
    pool: &DbPool,
    status: Option<&str>,
    execution_id: Option<&str>,
) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns(pool);
    let mut sql = format!("SELECT {cols} FROM sessions WHERE 1=1");

    let is_working = status == Some("working");
    let status_is_outcome = matches!(status, Some("completed" | "failed" | "canceled"));
    let is_submitted = status == Some("submitted");
    let is_input_required = status == Some("input-required");
    if is_working {
        sql.push_str(
            " AND outcome IS NULL AND (executor_state = 'running' OR \
             command_token IS NOT NULL OR \
             id IN (SELECT session_id FROM task_queue))",
        );
    } else if status_is_outcome {
        sql.push_str(" AND outcome = ?");
    } else if is_submitted {
        sql.push_str(" AND executor_state = 'unassigned' AND outcome IS NULL");
    } else if is_input_required {
        sql.push_str(
            " AND executor_state = 'idle' AND desired = 'run' AND outcome IS NULL \
             AND id NOT IN (SELECT session_id FROM task_queue)",
        );
    } else if status.is_some() {
        sql.push_str(" AND desired = ?");
    }
    if execution_id.is_some() {
        sql.push_str(" AND execution_id = ?");
    }
    sql.push_str(" ORDER BY created_at ASC");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    if !is_working
        && !is_submitted
        && !is_input_required
        && let Some(st) = status
    {
        q = q.bind(st);
    }
    if let Some(eid) = execution_id {
        q = q.bind(eid);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

/// List sessions that may need evaluation.
pub async fn find_reconcilable(pool: &DbPool) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns(pool);
    let sql = format!(
        "SELECT {cols} FROM sessions WHERE \
         outcome IS NULL \
         OR worker_id IS NOT NULL \
         OR command_token IS NOT NULL \
         OR (parent_notified = FALSE AND parent_session_id IS NOT NULL AND outcome = 'failed') \
         OR (outcome IS NOT NULL AND desired != 'terminate') \
         OR (parent_session_id IS NULL AND outcome IS NOT NULL \
             AND execution_id IN (SELECT id FROM executions WHERE outcome IS NULL)) \
         OR (outcome IS NOT NULL AND id IN (SELECT DISTINCT session_id FROM task_queue)) \
         OR (desired = 'terminate' AND outcome IS NOT NULL \
             AND id IN (SELECT parent_session_id FROM sessions \
                        WHERE outcome IS NULL AND desired != 'terminate')) \
         ORDER BY created_at ASC"
    );
    let prepared = pool.prepare_query(&sql);
    let rows = sqlx::query(&prepared)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find_reconcilable failed: {e}")))?;
    rows.into_iter().map(parse_session_row).collect()
}

/// List sessions with pending_turns.
pub async fn list_with_pending(
    pool: &DbPool,
    execution_id: Option<&str>,
) -> Result<Vec<(Session, i64)>, SchedulerError> {
    let cols = session_columns(pool);
    let mut sql = format!(
        "SELECT {cols}, COALESCE(tq.cnt, 0) as pending_turns \
         FROM sessions \
         LEFT JOIN ( \
           SELECT session_id, COUNT(*) as cnt FROM task_queue GROUP BY session_id \
         ) tq ON tq.session_id = sessions.id \
         WHERE 1=1"
    );
    if execution_id.is_some() {
        sql.push_str(" AND sessions.execution_id = ?");
    }
    sql.push_str(" ORDER BY sessions.created_at ASC");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);
    if let Some(eid) = execution_id {
        q = q.bind(eid);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions with pending failed: {e}")))?;

    rows.into_iter()
        .map(|row| {
            let pending: i64 = row.get("pending_turns");
            let session = parse_session_row(row)?;
            Ok((session, pending))
        })
        .collect()
}

pub async fn update_agent_session_id(
    pool: &DbPool,
    id: &str,
    agent_session_id: &str,
    worker_id: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET agent_session_id = ?, updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND worker_id = ?",
    );

    let result = sqlx::query(&query)
        .bind(agent_session_id)
        .bind(id)
        .bind(worker_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update agent_session_id failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("session not found: {id}")));
    }

    Ok(())
}

/// Count non-terminal sessions for an agent (used by agent delete guard)
pub async fn count_non_terminal_by_agent(
    pool: &DbPool,
    agent_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM sessions WHERE agent_id = ? AND outcome IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(agent_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("count non-terminal sessions failed: {e}"))
        })?;

    Ok(row.get::<i64, _>("cnt"))
}

/// Return all sessions in the subtree rooted at `root_id` (inclusive).
/// Uses a recursive CTE — works on both SQLite and PostgreSQL.
pub async fn get_subtree(pool: &DbPool, root_id: &str) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns_prefixed(pool, "s");

    let sql = format!(
        "WITH RECURSIVE subtree AS (\
            SELECT id FROM sessions WHERE id = ? \
            UNION ALL \
            SELECT s.id FROM sessions s \
            INNER JOIN subtree st ON s.parent_session_id = st.id \
        ) \
        SELECT {cols} \
        FROM sessions s \
        INNER JOIN subtree st ON s.id = st.id \
        ORDER BY s.created_at ASC"
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(root_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("get subtree failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

/// Get direct children of a session.
pub async fn get_children(pool: &DbPool, parent_id: &str) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns_prefixed(pool, "s");
    let sql = format!(
        "SELECT {cols} FROM sessions s WHERE s.parent_session_id = ? ORDER BY s.created_at ASC"
    );
    let query = pool.prepare_query(&sql);
    let rows = sqlx::query(&query)
        .bind(parent_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("get children failed: {e}")))?;
    rows.into_iter().map(parse_session_row).collect()
}

/// Get slugs of all siblings (children of the same parent), including terminal.
/// Includes terminal sessions to match the DB unique index scope and prevent
/// slug reuse that would cause ambiguity in message history.
pub async fn sibling_slugs(
    pool: &DbPool,
    parent_session_id: &str,
) -> Result<Vec<String>, SchedulerError> {
    let query =
        pool.prepare_query("SELECT slug FROM sessions WHERE parent_session_id = ? AND slug != ''");
    let rows = sqlx::query_scalar::<_, String>(&query)
        .bind(parent_session_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("sibling slugs query failed: {e}")))?;
    Ok(rows)
}

/// Get slugs of all root sessions in an execution.
/// Used to ensure slug uniqueness among root sessions within the same execution.
pub async fn root_slugs(pool: &DbPool, execution_id: &str) -> Result<Vec<String>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT slug FROM sessions WHERE execution_id = ? AND parent_session_id IS NULL AND slug != ''",
    );
    let rows = sqlx::query_scalar::<_, String>(&query)
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("root slugs query failed: {e}")))?;
    Ok(rows)
}

/// Increment recovery_attempts for a session
pub async fn increment_recovery_attempts(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET recovery_attempts = recovery_attempts + 1, \
         updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    );
    sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("increment recovery_attempts failed: {e}"))
        })?;
    Ok(())
}

/// Touch updated_at only (heartbeat/liveness signal).
pub async fn touch_updated_at(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query =
        pool.prepare_query("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?");
    sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("touch updated_at failed: {e}")))?;
    Ok(())
}

/// Clear worktree_path for a session, only if fully finalized.
/// Requires outcome set + no worker + no pending command (TOCTOU guard).
/// Returns rows_affected so the caller can disambiguate 0-row outcomes.
pub async fn clear_worktree_path(pool: &DbPool, id: &str) -> Result<u64, SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET worktree_path = NULL, updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND outcome IS NOT NULL AND worker_id IS NULL \
         AND command_token IS NULL AND worktree_path IS NOT NULL",
    );
    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("clear worktree_path failed: {e}")))?;

    Ok(result.rows_affected())
}

/// Find all sessions assigned to a specific worker
pub async fn find_sessions_for_worker(
    pool: &DbPool,
    worker_id: &str,
) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns(pool);
    let sql = format!("SELECT {cols} FROM sessions WHERE worker_id = ? ORDER BY created_at ASC");
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(worker_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find sessions for worker failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

/// Find an unassigned session with pending work.
pub async fn find_unassigned_with_work(pool: &DbPool) -> Result<Option<Session>, SchedulerError> {
    let cols = session_columns_prefixed(pool, "s");
    let sql = format!(
        "SELECT {cols} FROM sessions s \
         JOIN executions e ON e.id = s.execution_id \
         WHERE s.desired = 'run' AND s.executor_state = 'unassigned' AND s.outcome IS NULL \
         AND s.command_token IS NULL AND s.worker_id IS NULL \
         AND e.desired = 'run' AND e.outcome IS NULL \
         AND (EXISTS (SELECT 1 FROM task_queue tq WHERE tq.session_id = s.id) \
              OR s.agent_session_id IS NOT NULL) \
         ORDER BY s.created_at ASC LIMIT 1"
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find unassigned with work failed: {e}")))?;

    match row {
        Some(r) => Ok(Some(parse_session_row(r)?)),
        None => Ok(None),
    }
}

/// Count sessions that could be assigned to a worker immediately.
/// Same predicate as find_unassigned_with_work() but as COUNT(*).
pub async fn count_claimable(pool: &DbPool) -> Result<usize, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM sessions s \
         JOIN executions e ON e.id = s.execution_id \
         WHERE s.desired = 'run' \
           AND s.executor_state = 'unassigned' \
           AND s.outcome IS NULL \
           AND e.desired = 'run' \
           AND e.outcome IS NULL \
           AND s.worker_id IS NULL \
           AND s.command_token IS NULL \
           AND (s.agent_session_id IS NOT NULL \
                OR EXISTS (SELECT 1 FROM task_queue tq WHERE tq.session_id = s.id))",
    );
    let count: i64 = sqlx::query_scalar(&query)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count_claimable: {e}")))?;
    Ok(count as usize)
}

/// Find crashed sessions with no assigned worker (used during startup recovery).
pub async fn find_crashed_workerless(pool: &DbPool) -> Result<Vec<Session>, SchedulerError> {
    let cols = session_columns_prefixed(pool, "s");
    let sql = format!(
        "SELECT {cols} FROM sessions s \
         WHERE s.desired = 'run' AND s.executor_state = 'crashed' \
         AND s.worker_id IS NULL AND s.outcome IS NULL"
    );
    let query = pool.prepare_query(&sql);
    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find_crashed_workerless failed: {e}")))?;
    rows.into_iter().map(parse_session_row).collect()
}

/// Count pending turns (task_queue entries) for a session
pub async fn count_pending_turns(pool: &DbPool, session_id: &str) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query("SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ?");
    let row = sqlx::query(&query)
        .bind(session_id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count pending turns failed: {e}")))?;
    Ok(row.get::<i64, _>("cnt"))
}

#[derive(Debug, Serialize)]
pub struct SessionDiscoveryEntry {
    pub session_id: String,
    pub agent_id: String,
    pub agent_name: String,
    pub slug: String,
    pub desired: String,
    pub executor_state: String,
    pub outcome: Option<String>,
    pub parent_session_id: Option<String>,
    pub depth: i64,
    #[serde(skip)]
    pub command_token: Option<String>,
    #[serde(skip)]
    pub pending_turns: i64,
}

pub async fn list_discovery_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<SessionDiscoveryEntry>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT s.id, s.agent_id, a.name as agent_name, s.slug, \
         s.desired, s.executor_state, s.outcome, \
         s.parent_session_id, s.execution_id, s.command_token, \
         COALESCE(tq.cnt, 0) as pending_turns \
         FROM sessions s \
         JOIN agents a ON s.agent_id = a.id \
         LEFT JOIN ( \
           SELECT session_id, COUNT(*) as cnt FROM task_queue \
           WHERE execution_id = ? GROUP BY session_id \
         ) tq ON tq.session_id = s.id \
         WHERE s.execution_id = ? \
         ORDER BY s.created_at ASC",
    );

    let rows = sqlx::query(&sql)
        .bind(execution_id)
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list discovery sessions failed: {e}")))?;

    let mut entries = Vec::new();
    for row in &rows {
        let session_id: String = row.get("id");
        let exec_id: String = row.get("execution_id");
        let depth = compute_depth(pool, &session_id, &exec_id).await?;
        entries.push(SessionDiscoveryEntry {
            session_id,
            agent_id: row.get("agent_id"),
            agent_name: row.get::<String, _>("agent_name"),
            slug: row.get("slug"),
            desired: row.get("desired"),
            executor_state: row.get("executor_state"),
            outcome: row.get("outcome"),
            parent_session_id: row.get("parent_session_id"),
            depth,
            command_token: row.get("command_token"),
            pending_turns: row.get("pending_turns"),
        });
    }

    Ok(entries)
}

/// Read a session inside an existing transaction.
/// Use this instead of get_by_id() inside tx blocks to avoid SQLite max_connections=1 deadlock.
pub async fn get_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    id: &str,
) -> Result<Session, SchedulerError> {
    let cols = session_columns(pool);
    let sql = format!("SELECT {cols} FROM sessions WHERE id = ?");
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(&mut **tx)
        .await
        .map_err(|e| map_db_error("session", id, e))?;

    parse_session_row(row)
}

fn parse_session_row(row: sqlx::any::AnyRow) -> Result<Session, SchedulerError> {
    Ok(Session {
        id: row.get("id"),
        execution_id: row.get("execution_id"),
        parent_session_id: row.get("parent_session_id"),
        agent_id: row.get("agent_id"),
        agent_session_id: row.get("agent_session_id"),
        cwd: row.get("cwd"),
        worktree_path: row.get("worktree_path"),
        base_commit_sha: row.get("base_commit_sha"),
        slug: row.get("slug"),
        recovery_attempts: row.get("recovery_attempts"),
        metadata: row.get("metadata"),
        desired: row.get("desired"),
        executor_state: row.get("executor_state"),
        outcome: row.get("outcome"),
        desired_by: row.get("desired_by"),
        desired_at: parse_optional_timestamp(&row, "desired_at"),
        command_token: row.get("command_token"),
        command_type: row.get("command_type"),
        command_at: parse_optional_timestamp(&row, "command_at"),
        command_has_payload: row
            .try_get::<bool, _>("command_has_payload")
            .unwrap_or_else(|_| row.get::<i32, _>("command_has_payload") != 0),
        worker_id: row.get("worker_id"),
        parent_notified: row
            .try_get::<bool, _>("parent_notified")
            .unwrap_or_else(|_| row.get::<i32, _>("parent_notified") != 0),
        continued_from_session_id: row.get("continued_from_session_id"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
        completed_at: parse_optional_timestamp(&row, "completed_at"),
    })
}
