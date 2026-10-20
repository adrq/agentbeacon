// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use sqlx::Row;

use crate::error::SchedulerError;

use super::{DbPool, TimestampColumn};

pub const PRINCIPAL_USER: &str = "user";
pub const PRINCIPAL_SESSION: &str = "session";
pub const PRINCIPAL_WORKER: &str = "worker";

/// A credential row joined with the principal it names, read in one statement.
#[derive(Debug, Clone)]
pub struct ResolvedCredential {
    pub principal_type: String,
    pub principal_id: String,
    pub expired: bool,
    /// Set when the row names a user that exists.
    pub user_exists: bool,
    /// Set when the row names a session that exists.
    pub session: Option<SessionBinding>,
}

/// The session a session credential names, with its execution's state.
#[derive(Debug, Clone)]
pub struct SessionBinding {
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub session_desired: String,
    pub session_outcome: Option<String>,
    pub execution_desired: String,
    pub execution_outcome: Option<String>,
    pub project_id: Option<String>,
}

impl SessionBinding {
    /// True while both the session and its execution are live.
    pub fn is_live(&self) -> bool {
        is_live(&self.session_desired, self.session_outcome.as_deref())
            && is_live(&self.execution_desired, self.execution_outcome.as_deref())
    }
}

/// True while a session or execution row is live: no terminate requested and no outcome.
pub fn is_live(desired: &str, outcome: Option<&str>) -> bool {
    desired != "terminate" && outcome.is_none()
}

/// One of a user's logins, as listed to its owner.
#[derive(Clone)]
pub struct Login {
    pub token_hash: String,
    pub label: Option<String>,
    pub created_at: String,
    pub expires_at: Option<String>,
}

fn expired_sql() -> &'static str {
    "CASE WHEN c.expires_at IS NOT NULL AND c.expires_at <= CURRENT_TIMESTAMP THEN 1 ELSE 0 END"
}

fn expires_at_sql(pool: &DbPool) -> &'static str {
    if pool.is_postgres() {
        "CURRENT_TIMESTAMP + (CAST(? AS BIGINT) * INTERVAL '1 second')"
    } else {
        "datetime('now', '+' || CAST(? AS TEXT) || ' seconds')"
    }
}

fn resolve_sql() -> String {
    format!(
        "SELECT c.principal_type AS principal_type, c.principal_id AS principal_id, \
                {expired} AS expired, \
                u.id AS user_id, \
                s.id AS session_id, s.execution_id AS execution_id, \
                s.parent_session_id AS parent_session_id, \
                s.desired AS session_desired, s.outcome AS session_outcome, \
                e.desired AS execution_desired, e.outcome AS execution_outcome, \
                e.project_id AS project_id \
         FROM credentials c \
         LEFT JOIN users u ON c.principal_type = 'user' AND u.id = c.principal_id \
         LEFT JOIN sessions s ON c.principal_type = 'session' AND s.id = c.principal_id \
         LEFT JOIN executions e ON e.id = s.execution_id \
         WHERE c.token_hash = ?",
        expired = expired_sql()
    )
}

fn resolved_from_row(row: &sqlx::any::AnyRow) -> ResolvedCredential {
    let expired: i64 = row
        .try_get::<i64, _>("expired")
        .unwrap_or_else(|_| row.try_get::<i32, _>("expired").map(i64::from).unwrap_or(0));
    let user_id: Option<String> = row.get("user_id");
    let session_id: Option<String> = row.get("session_id");
    let session = match (
        session_id,
        row.get::<Option<String>, _>("execution_desired"),
    ) {
        (Some(_), Some(execution_desired)) => Some(SessionBinding {
            execution_id: row.get("execution_id"),
            parent_session_id: row.get("parent_session_id"),
            session_desired: row.get("session_desired"),
            session_outcome: row.get("session_outcome"),
            execution_desired,
            execution_outcome: row.get("execution_outcome"),
            project_id: row.get("project_id"),
        }),
        _ => None,
    };
    ResolvedCredential {
        principal_type: row.get("principal_type"),
        principal_id: row.get("principal_id"),
        expired: expired != 0,
        user_exists: user_id.is_some(),
        session,
    }
}

/// Look a credential up by its hash and resolve the principal it names.
pub async fn resolve(
    pool: &DbPool,
    token_hash: &str,
) -> Result<Option<ResolvedCredential>, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query(&resolve_sql()))
        .bind(token_hash)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("resolve credential failed: {e}")))?;
    Ok(row.as_ref().map(resolved_from_row))
}

/// The same lookup, read on a held transaction.
pub async fn resolve_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    token_hash: &str,
) -> Result<Option<ResolvedCredential>, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query(&resolve_sql()))
        .bind(token_hash)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("resolve credential failed: {e}")))?;
    Ok(row.as_ref().map(resolved_from_row))
}

/// The project of a live session whose credential is still `token_hash`, read on a held transaction.
///
/// `None` when the credential was replaced or deleted, or the session or its
/// execution is no longer live.
pub async fn live_session_project_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    session_id: &str,
    token_hash: &str,
) -> Result<Option<Option<String>>, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query(
        "SELECT e.project_id AS project_id FROM credentials c \
         JOIN sessions s ON s.id = c.principal_id \
         JOIN executions e ON e.id = s.execution_id \
         WHERE c.token_hash = ? AND c.principal_type = 'session' AND c.principal_id = ? \
           AND s.desired <> 'terminate' AND s.outcome IS NULL \
           AND e.desired <> 'terminate' AND e.outcome IS NULL",
    ))
    .bind(token_hash)
    .bind(session_id)
    .fetch_optional(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("check session credential failed: {e}")))?;
    Ok(row.map(|r| r.get::<Option<String>, _>("project_id")))
}

/// True while `session_id` is live and its credential is still `token_hash`, read on a held transaction.
pub async fn session_credential_live_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    session_id: &str,
    token_hash: Option<&str>,
) -> Result<bool, SchedulerError> {
    let Some(token_hash) = token_hash else {
        return Ok(false);
    };
    Ok(live_session_project_in_tx(tx, pool, session_id, token_hash)
        .await?
        .is_some())
}

/// Insert a credential row. `ttl_secs` sets `expires_at` relative to now.
pub async fn insert_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    token_hash: &str,
    principal_type: &str,
    principal_id: &str,
    label: Option<&str>,
    ttl_secs: Option<i64>,
) -> Result<(), SchedulerError> {
    let result = match ttl_secs {
        Some(ttl) => {
            let sql = format!(
                "INSERT INTO credentials (token_hash, principal_type, principal_id, label, expires_at) \
                 VALUES (?, ?, ?, ?, {})",
                expires_at_sql(pool)
            );
            sqlx::query(&pool.prepare_query(&sql))
                .bind(token_hash)
                .bind(principal_type)
                .bind(principal_id)
                .bind(label)
                .bind(ttl)
                .execute(&mut **tx)
                .await
        }
        None => {
            sqlx::query(&pool.prepare_query(
                "INSERT INTO credentials (token_hash, principal_type, principal_id, label) \
                 VALUES (?, ?, ?, ?)",
            ))
            .bind(token_hash)
            .bind(principal_type)
            .bind(principal_id)
            .bind(label)
            .execute(&mut **tx)
            .await
        }
    };
    result.map_err(|e| SchedulerError::Database(format!("insert credential failed: {e}")))?;
    Ok(())
}

/// Insert a credential row outside any caller transaction.
pub async fn insert(
    pool: &DbPool,
    token_hash: &str,
    principal_type: &str,
    principal_id: &str,
    label: Option<&str>,
    ttl_secs: Option<i64>,
) -> Result<(), SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;
    insert_in_tx(
        &mut tx,
        pool,
        token_hash,
        principal_type,
        principal_id,
        label,
        ttl_secs,
    )
    .await?;
    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit failed: {e}")))
}

/// Replace a session's credential with `token_hash`.
pub async fn replace_session_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    session_id: &str,
    token_hash: &str,
) -> Result<(), SchedulerError> {
    sqlx::query(&pool.prepare_query(
        "DELETE FROM credentials WHERE principal_type = 'session' AND principal_id = ?",
    ))
    .bind(session_id)
    .execute(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("delete session credential failed: {e}")))?;
    insert_in_tx(
        tx,
        pool,
        token_hash,
        PRINCIPAL_SESSION,
        session_id,
        None,
        None,
    )
    .await
}

/// Delete one credential row of the given principal type. Returns the rows deleted.
pub async fn delete_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    token_hash: &str,
    principal_type: &str,
) -> Result<u64, SchedulerError> {
    let result = sqlx::query(
        &pool.prepare_query("DELETE FROM credentials WHERE token_hash = ? AND principal_type = ?"),
    )
    .bind(token_hash)
    .bind(principal_type)
    .execute(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("delete credential failed: {e}")))?;
    Ok(result.rows_affected())
}

/// Delete every login of a user.
pub async fn delete_user_logins_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    user_id: &str,
) -> Result<(), SchedulerError> {
    sqlx::query(&pool.prepare_query(
        "DELETE FROM credentials WHERE principal_type = 'user' AND principal_id = ?",
    ))
    .bind(user_id)
    .execute(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("delete logins failed: {e}")))?;
    Ok(())
}

/// Delete one of a user's logins. Returns whether a row was deleted.
pub async fn delete_user_login(
    pool: &DbPool,
    user_id: &str,
    token_hash: &str,
) -> Result<bool, SchedulerError> {
    let result = sqlx::query(&pool.prepare_query(
        "DELETE FROM credentials \
         WHERE token_hash = ? AND principal_type = 'user' AND principal_id = ?",
    ))
    .bind(token_hash)
    .bind(user_id)
    .execute(pool.as_ref())
    .await
    .map_err(|e| SchedulerError::Database(format!("delete login failed: {e}")))?;
    Ok(result.rows_affected() > 0)
}

/// A user's unexpired logins, oldest first.
pub async fn list_user_logins(pool: &DbPool, user_id: &str) -> Result<Vec<Login>, SchedulerError> {
    let created = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", "c.created_at");
    let expires = pool
        .format_timestamp(TimestampColumn::ExpiresAt)
        .replace("expires_at", "c.expires_at");
    let sql = format!(
        "SELECT c.token_hash AS token_hash, c.label AS label, \
                {created} AS created_at, {expires} AS expires_at \
         FROM credentials c \
         WHERE c.principal_type = 'user' AND c.principal_id = ? AND {expired} = 0 \
         ORDER BY c.created_at ASC, c.token_hash ASC",
        expired = expired_sql()
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(user_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list logins failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| Login {
            token_hash: r.get("token_hash"),
            label: r.get("label"),
            created_at: r.get("created_at"),
            expires_at: r.get("expires_at"),
        })
        .collect())
}
