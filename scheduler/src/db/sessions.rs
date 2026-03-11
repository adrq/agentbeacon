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
    pub status: String,
    pub metadata: String, // JSON
    pub slug: String,
    pub recovery_attempts: i64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
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
    slug: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, worktree_path, status, slug) VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(execution_id)
        .bind(parent_session_id)
        .bind(agent_id)
        .bind(cwd)
        .bind(worktree_path)
        .bind(slug)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create session failed: {e}")))?;

    Ok(())
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Session, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, worktree_path, status, metadata, slug, recovery_attempts, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE id = ?",
        created_fmt, updated_fmt, completed_fmt
    );
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
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, worktree_path, status, metadata, slug, recovery_attempts, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE execution_id = ? ORDER BY created_at ASC",
        created_fmt, updated_fmt, completed_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

pub async fn update_status(pool: &DbPool, id: &str, status: &str) -> Result<(), SchedulerError> {
    let is_terminal = matches!(status, "completed" | "failed" | "canceled");

    let query = if is_terminal {
        pool.prepare_query(
            "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        )
    } else {
        pool.prepare_query(
            "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP, completed_at = NULL WHERE id = ?",
        )
    };

    let result = sqlx::query(&query)
        .bind(status)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update session status failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("session not found: {id}")));
    }

    Ok(())
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
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, worktree_path, status, slug) \
         SELECT ?, ?, ?, ?, ?, ?, 'submitted', ? \
         WHERE (SELECT COUNT(*) FROM sessions \
                WHERE parent_session_id = ? \
                AND status NOT IN ('completed', 'failed', 'canceled')) < ?",
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
        "SELECT COUNT(*) as cnt FROM sessions WHERE parent_session_id = ? AND status NOT IN ('completed', 'failed', 'canceled')",
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

pub async fn list_filtered(
    pool: &DbPool,
    status: Option<&str>,
    execution_id: Option<&str>,
) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let mut sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, worktree_path, status, metadata, slug, recovery_attempts, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE 1=1",
        created_fmt, updated_fmt, completed_fmt
    );

    if status.is_some() {
        sql.push_str(" AND status = ?");
    }
    if execution_id.is_some() {
        sql.push_str(" AND execution_id = ?");
    }
    sql.push_str(" ORDER BY created_at ASC");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    if let Some(st) = status {
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

pub async fn update_agent_session_id(
    pool: &DbPool,
    id: &str,
    agent_session_id: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET agent_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    );

    let result = sqlx::query(&query)
        .bind(agent_session_id)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update agent_session_id failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("session not found: {id}")));
    }

    Ok(())
}

pub async fn find_assignable(pool: &DbPool) -> Result<Option<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "SELECT id, execution_id, parent_session_id, agent_id, agent_session_id, cwd, worktree_path, status, metadata, slug, recovery_attempts, {} as created_at, {} as updated_at, {} as completed_at FROM sessions WHERE status = 'submitted' ORDER BY created_at ASC, id ASC LIMIT 1",
        created_fmt, updated_fmt, completed_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find assignable session failed: {e}")))?;

    match row {
        Some(r) => Ok(Some(parse_session_row(r)?)),
        None => Ok(None),
    }
}

pub async fn claim_assignable(pool: &DbPool, id: &str) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET status = 'working', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'submitted'",
    );

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("claim assignable session failed: {e}")))?;

    Ok(result.rows_affected() > 0)
}

/// Count non-terminal sessions for an agent (used by agent delete guard)
pub async fn count_non_terminal_by_agent(
    pool: &DbPool,
    agent_id: &str,
) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM sessions WHERE agent_id = ? AND status NOT IN ('completed', 'failed', 'canceled')",
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
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let sql = format!(
        "WITH RECURSIVE subtree AS (\
            SELECT id FROM sessions WHERE id = ? \
            UNION ALL \
            SELECT s.id FROM sessions s \
            INNER JOIN subtree st ON s.parent_session_id = st.id \
        ) \
        SELECT s.id, s.execution_id, s.parent_session_id, s.agent_id, \
               s.agent_session_id, s.cwd, s.worktree_path, s.status, \
               s.metadata, s.slug, s.recovery_attempts, \
               {cr} as created_at, {up} as updated_at, \
               {co} as completed_at \
        FROM sessions s \
        INNER JOIN subtree st ON s.id = st.id \
        ORDER BY s.created_at ASC",
        cr = created_fmt,
        up = updated_fmt,
        co = completed_fmt
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(root_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("get subtree failed: {e}")))?;

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

/// Touch updated_at for heartbeat — lets the recovery scan distinguish live workers
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

/// Clear worktree_path for a session, only if it's in a terminal state.
/// Uses a WHERE guard for atomicity (prevents TOCTOU race).
pub async fn clear_worktree_path(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE sessions SET worktree_path = NULL, updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND status IN ('completed', 'failed', 'canceled')",
    );
    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("clear worktree_path failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::Conflict(
            "session not found or not in terminal state".to_string(),
        ));
    }
    Ok(())
}

/// Find sessions eligible for crash recovery (Tier 2).
/// Returns sessions that are working/input-required with an agent_session_id,
/// belonging to resumable agent types, not updated since `updated_before`.
pub async fn find_recoverable(
    pool: &DbPool,
    max_recovery_attempts: i64,
    updated_before: &str,
) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    // PostgreSQL TIMESTAMPTZ can't compare directly with text; cast the bound param
    let ts_cast = if pool.is_postgres() {
        "::timestamptz"
    } else {
        ""
    };

    let sql = format!(
        "SELECT s.id, s.execution_id, s.parent_session_id, s.agent_id, \
               s.agent_session_id, s.cwd, s.worktree_path, s.status, \
               s.metadata, s.slug, s.recovery_attempts, \
               {cr} as created_at, {up} as updated_at, \
               {co} as completed_at \
        FROM sessions s \
        JOIN agents a ON s.agent_id = a.id \
        JOIN executions e ON s.execution_id = e.id \
        WHERE s.status IN ('working', 'input-required') \
          AND s.agent_session_id IS NOT NULL \
          AND s.cwd IS NOT NULL \
          AND s.recovery_attempts < ? \
          AND a.agent_type IN ('claude_sdk', 'copilot_sdk') \
          AND a.deleted_at IS NULL \
          AND e.status NOT IN ('completed', 'failed', 'canceled') \
          AND s.updated_at < ?{ts_cast}",
        cr = created_fmt.replace("created_at", "s.created_at"),
        up = updated_fmt.replace("updated_at", "s.updated_at"),
        co = completed_fmt.replace("completed_at", "s.completed_at"),
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(max_recovery_attempts)
        .bind(updated_before)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find recoverable sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

/// Find sessions that have exhausted their recovery budget (for permanent failure).
pub async fn find_over_budget(
    pool: &DbPool,
    max_recovery_attempts: i64,
    updated_before: &str,
) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    // PostgreSQL TIMESTAMPTZ can't compare directly with text; cast the bound param
    let ts_cast = if pool.is_postgres() {
        "::timestamptz"
    } else {
        ""
    };

    let sql = format!(
        "SELECT s.id, s.execution_id, s.parent_session_id, s.agent_id, \
               s.agent_session_id, s.cwd, s.worktree_path, s.status, \
               s.metadata, s.slug, s.recovery_attempts, \
               {cr} as created_at, {up} as updated_at, \
               {co} as completed_at \
        FROM sessions s \
        JOIN agents a ON s.agent_id = a.id \
        JOIN executions e ON s.execution_id = e.id \
        WHERE s.status IN ('working', 'input-required') \
          AND s.agent_session_id IS NOT NULL \
          AND s.recovery_attempts >= ? \
          AND a.agent_type IN ('claude_sdk', 'copilot_sdk') \
          AND a.deleted_at IS NULL \
          AND e.status NOT IN ('completed', 'failed', 'canceled') \
          AND s.updated_at < ?{ts_cast}",
        cr = created_fmt.replace("created_at", "s.created_at"),
        up = updated_fmt.replace("updated_at", "s.updated_at"),
        co = completed_fmt.replace("completed_at", "s.completed_at"),
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(max_recovery_attempts)
        .bind(updated_before)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("find over-budget sessions failed: {e}")))?;

    rows.into_iter().map(parse_session_row).collect()
}

/// Find stale sessions for non-resumable agent types (e.g., ACP).
/// These cannot be recovered via resume — they must be permanently failed.
/// Also catches deleted agents of any type (can't resume if agent config is gone).
pub async fn find_stale_non_resumable(
    pool: &DbPool,
    updated_before: &str,
) -> Result<Vec<Session>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let completed_fmt = pool.format_timestamp(TimestampColumn::CompletedAt);

    let ts_cast = if pool.is_postgres() {
        "::timestamptz"
    } else {
        ""
    };

    let sql = format!(
        "SELECT s.id, s.execution_id, s.parent_session_id, s.agent_id, \
               s.agent_session_id, s.cwd, s.worktree_path, s.status, \
               s.metadata, s.slug, s.recovery_attempts, \
               {cr} as created_at, {up} as updated_at, \
               {co} as completed_at \
        FROM sessions s \
        JOIN agents a ON s.agent_id = a.id \
        JOIN executions e ON s.execution_id = e.id \
        WHERE s.status IN ('working', 'input-required') \
          AND (a.agent_type NOT IN ('claude_sdk', 'copilot_sdk') OR a.deleted_at IS NOT NULL) \
          AND e.status NOT IN ('completed', 'failed', 'canceled') \
          AND s.updated_at < ?{ts_cast}",
        cr = created_fmt.replace("created_at", "s.created_at"),
        up = updated_fmt.replace("updated_at", "s.updated_at"),
        co = completed_fmt.replace("completed_at", "s.completed_at"),
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(updated_before)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("find stale non-resumable sessions failed: {e}"))
        })?;

    rows.into_iter().map(parse_session_row).collect()
}

/// Atomically fail a stale over-budget session.
/// Sets status to 'failed' so the session is terminal even if downstream
/// cascade/notify (handle_session_failure) encounters errors.
/// Returns true if the session was still stale and transitioned, false if it
/// was updated between the SELECT scan and now (worker reconnected).
pub async fn try_claim_for_failure(
    pool: &DbPool,
    id: &str,
    updated_before: &str,
) -> Result<bool, SchedulerError> {
    let ts_cast = if pool.is_postgres() {
        "::timestamptz"
    } else {
        ""
    };
    let sql = format!(
        "UPDATE sessions SET status = 'failed', \
         updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND status IN ('working', 'input-required') \
         AND updated_at < ?{ts_cast}"
    );
    let query = pool.prepare_query(&sql);
    let result = sqlx::query(&query)
        .bind(id)
        .bind(updated_before)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("try_claim_for_failure failed: {e}")))?;
    Ok(result.rows_affected() > 0)
}

#[derive(Debug, Serialize)]
pub struct SessionDiscoveryEntry {
    pub session_id: String,
    pub agent_id: String,
    pub agent_name: String,
    pub slug: String,
    pub status: String,
    pub parent_session_id: Option<String>,
    pub depth: i64,
}

pub async fn list_discovery_by_execution(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<SessionDiscoveryEntry>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT s.id, s.agent_id, a.name as agent_name, s.slug, s.status, \
         s.parent_session_id, s.execution_id \
         FROM sessions s \
         JOIN agents a ON s.agent_id = a.id \
         WHERE s.execution_id = ? \
         ORDER BY s.created_at ASC",
    );

    let rows = sqlx::query(&sql)
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
            status: row.get("status"),
            parent_session_id: row.get("parent_session_id"),
            depth,
        });
    }

    Ok(entries)
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
        status: row.get("status"),
        metadata: row.get("metadata"),
        slug: row.get("slug"),
        recovery_attempts: row.get("recovery_attempts"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
        completed_at: parse_optional_timestamp(&row, "completed_at"),
    })
}
