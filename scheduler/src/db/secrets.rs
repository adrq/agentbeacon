// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Project secrets and their attachment to executions.

use chrono::{DateTime, Utc};
use sqlx::Row;
use uuid::Uuid;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// A secret's value. `Debug` prints `[redacted]`; there is no `Display` or `Serialize`.
pub struct SecretValue(String);

impl SecretValue {
    pub fn new(value: String) -> Self {
        Self(value)
    }

    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for SecretValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("SecretValue([redacted])")
    }
}

/// A secret without its value.
#[derive(Debug, Clone)]
pub struct SecretMeta {
    pub id: String,
    pub project_id: String,
    pub name: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// An attachment of a secret to a running execution.
#[derive(Debug, Clone)]
pub struct Attachment {
    pub execution_id: String,
    pub attached_at: DateTime<Utc>,
}

/// A secret attached to an execution, by name.
#[derive(Debug, Clone)]
pub struct AttachedSecret {
    pub name: String,
    pub attached_at: DateTime<Utc>,
}

#[derive(Debug)]
pub enum CreateOutcome {
    Created(SecretMeta),
    NameTaken,
}

#[derive(Debug)]
pub enum DeleteOutcome {
    Deleted,
    NotFound,
    AttachedToLive(Vec<String>),
}

#[derive(Debug, PartialEq)]
pub enum AttachOutcome {
    Attached,
    AlreadyAttached,
    NotFound,
}

#[derive(Debug)]
pub enum FetchOutcome {
    Value(SecretValue),
    NotAttached,
    NotLive,
}

/// An execution that is neither terminating nor ended.
const EXECUTION_LIVE: &str = "e.desired <> 'terminate' AND e.outcome IS NULL";

fn meta_columns(pool: &DbPool) -> String {
    let created = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated = pool.format_timestamp(TimestampColumn::UpdatedAt);
    format!("id, project_id, name, {created} as created_at, {updated} as updated_at")
}

fn parse_meta(row: &sqlx::any::AnyRow) -> Result<SecretMeta, SchedulerError> {
    Ok(SecretMeta {
        id: row.get("id"),
        project_id: row.get("project_id"),
        name: row.get("name"),
        created_at: parse_timestamp(row, "created_at")?,
        updated_at: parse_timestamp(row, "updated_at")?,
    })
}

fn lock_suffix(pool: &DbPool) -> &'static str {
    if pool.is_postgres() {
        " FOR UPDATE"
    } else {
        ""
    }
}

fn is_unique_violation(e: &sqlx::Error) -> bool {
    let msg = e.to_string().to_lowercase();
    msg.contains("unique") || msg.contains("duplicate key")
}

pub async fn create(
    pool: &DbPool,
    project_id: &str,
    name: &str,
    value: &SecretValue,
) -> Result<CreateOutcome, SchedulerError> {
    let id = Uuid::new_v4().to_string();
    let result = sqlx::query(
        &pool
            .prepare_query("INSERT INTO secrets (id, project_id, name, value) VALUES (?, ?, ?, ?)"),
    )
    .bind(&id)
    .bind(project_id)
    .bind(name)
    .bind(value.expose())
    .execute(pool.as_ref())
    .await;
    match result {
        Ok(_) => {}
        Err(e) if is_unique_violation(&e) => return Ok(CreateOutcome::NameTaken),
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "create secret failed: {e}"
            )));
        }
    }
    let meta = get_active(pool, project_id, name)
        .await?
        .ok_or_else(|| SchedulerError::Database("created secret not found".into()))?;
    Ok(CreateOutcome::Created(meta))
}

pub async fn get_active(
    pool: &DbPool,
    project_id: &str,
    name: &str,
) -> Result<Option<SecretMeta>, SchedulerError> {
    let sql = format!(
        "SELECT {} FROM secrets WHERE project_id = ? AND name = ? AND deleted_at IS NULL",
        meta_columns(pool)
    );
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .bind(name)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("get secret failed: {e}")))?;
    row.as_ref().map(parse_meta).transpose()
}

/// Every active secret of a project, by name, with its attachments to live executions.
pub async fn list_by_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<(SecretMeta, Vec<Attachment>)>, SchedulerError> {
    let sql = format!(
        "SELECT {} FROM secrets WHERE project_id = ? AND deleted_at IS NULL ORDER BY name",
        meta_columns(pool)
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list secrets failed: {e}")))?;
    let metas = rows.iter().map(parse_meta).collect::<Result<Vec<_>, _>>()?;

    let attached = pool.format_timestamp(TimestampColumn::AttachedAt);
    let sql = format!(
        "SELECT a.secret_id as secret_id, a.execution_id as execution_id, {attached} as attached_at \
         FROM execution_secrets a \
         JOIN secrets s ON s.id = a.secret_id \
         JOIN executions e ON e.id = a.execution_id \
         WHERE s.project_id = ? AND s.deleted_at IS NULL AND a.detached_at IS NULL AND {EXECUTION_LIVE} \
         ORDER BY a.attached_at, a.execution_id"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list secret attachments failed: {e}")))?;

    let mut out: Vec<(SecretMeta, Vec<Attachment>)> =
        metas.into_iter().map(|m| (m, Vec::new())).collect();
    for row in &rows {
        let secret_id: String = row.get("secret_id");
        if let Some((_, list)) = out.iter_mut().find(|(m, _)| m.id == secret_id) {
            list.push(Attachment {
                execution_id: row.get("execution_id"),
                attached_at: parse_timestamp(row, "attached_at")?,
            });
        }
    }
    Ok(out)
}

/// Attachments of one secret to live executions.
pub async fn live_attachments(
    pool: &DbPool,
    secret_id: &str,
) -> Result<Vec<Attachment>, SchedulerError> {
    let attached = pool.format_timestamp(TimestampColumn::AttachedAt);
    let sql = format!(
        "SELECT a.execution_id as execution_id, {attached} as attached_at \
         FROM execution_secrets a JOIN executions e ON e.id = a.execution_id \
         WHERE a.secret_id = ? AND a.detached_at IS NULL AND {EXECUTION_LIVE} \
         ORDER BY a.attached_at, a.execution_id"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(secret_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list secret attachments failed: {e}")))?;
    rows.iter()
        .map(|row| {
            Ok(Attachment {
                execution_id: row.get("execution_id"),
                attached_at: parse_timestamp(row, "attached_at")?,
            })
        })
        .collect()
}

/// Replace a secret's value. `None` when no active secret has this name.
pub async fn rotate(
    pool: &DbPool,
    project_id: &str,
    name: &str,
    value: &SecretValue,
) -> Result<Option<SecretMeta>, SchedulerError> {
    let updated = sqlx::query(&pool.prepare_query(
        "UPDATE secrets SET value = ?, updated_at = CURRENT_TIMESTAMP \
         WHERE project_id = ? AND name = ? AND deleted_at IS NULL",
    ))
    .bind(value.expose())
    .bind(project_id)
    .bind(name)
    .execute(pool.as_ref())
    .await
    .map_err(|e| SchedulerError::Database(format!("rotate secret failed: {e}")))?
    .rows_affected();
    if updated == 0 {
        return Ok(None);
    }
    get_active(pool, project_id, name).await
}

/// Delete a secret unless it is attached to a live execution: clears the value and
/// marks the row deleted.
pub async fn delete(
    pool: &DbPool,
    project_id: &str,
    name: &str,
) -> Result<DeleteOutcome, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin delete secret failed: {e}")))?;
    let result = async {
        let sql = format!(
            "SELECT id FROM secrets WHERE project_id = ? AND name = ? AND deleted_at IS NULL{}",
            lock_suffix(pool)
        );
        let Some(row) = sqlx::query(&pool.prepare_query(&sql))
            .bind(project_id)
            .bind(name)
            .fetch_optional(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("lock secret failed: {e}")))?
        else {
            return Ok(DeleteOutcome::NotFound);
        };
        let secret_id: String = row.get("id");

        let sql = format!(
            "SELECT a.execution_id as execution_id FROM execution_secrets a \
             JOIN executions e ON e.id = a.execution_id \
             WHERE a.secret_id = ? AND a.detached_at IS NULL AND {EXECUTION_LIVE} \
             ORDER BY a.execution_id"
        );
        let live = sqlx::query(&pool.prepare_query(&sql))
            .bind(&secret_id)
            .fetch_all(&mut *tx)
            .await
            .map_err(|e| {
                SchedulerError::Database(format!("check secret attachments failed: {e}"))
            })?;
        if !live.is_empty() {
            return Ok(DeleteOutcome::AttachedToLive(
                live.iter().map(|r| r.get("execution_id")).collect(),
            ));
        }

        sqlx::query(&pool.prepare_query(
            "UPDATE secrets SET value = NULL, deleted_at = CURRENT_TIMESTAMP, \
             updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        ))
        .bind(&secret_id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("delete secret failed: {e}")))?;
        Ok(DeleteOutcome::Deleted)
    }
    .await;

    match result {
        Ok(DeleteOutcome::Deleted) => {
            tx.commit().await.map_err(|e| {
                SchedulerError::Database(format!("commit delete secret failed: {e}"))
            })?;
            Ok(DeleteOutcome::Deleted)
        }
        other => {
            let _ = tx.rollback().await;
            other
        }
    }
}

/// Lock an active secret row by id; false when it was deleted.
async fn lock_active_by_id_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    secret_id: &str,
) -> Result<bool, SchedulerError> {
    let sql = format!(
        "SELECT id FROM secrets WHERE id = ? AND deleted_at IS NULL{}",
        lock_suffix(pool)
    );
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(secret_id)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("lock secret failed: {e}")))?;
    Ok(row.is_some())
}

/// Insert an attachment unless an active one exists. True when a row was inserted.
async fn insert_attachment_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    execution_id: &str,
    secret_id: &str,
) -> Result<bool, SchedulerError> {
    let existing = sqlx::query(&pool.prepare_query(
        "SELECT id FROM execution_secrets \
         WHERE execution_id = ? AND secret_id = ? AND detached_at IS NULL",
    ))
    .bind(execution_id)
    .bind(secret_id)
    .fetch_optional(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("check attachment failed: {e}")))?;
    if existing.is_some() {
        return Ok(false);
    }
    sqlx::query(&pool.prepare_query(
        "INSERT INTO execution_secrets (id, execution_id, secret_id) VALUES (?, ?, ?)",
    ))
    .bind(Uuid::new_v4().to_string())
    .bind(execution_id)
    .bind(secret_id)
    .execute(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("attach secret failed: {e}")))?;
    Ok(true)
}

/// Attach the active secret `name` of `project_id` to an execution. Every read and
/// write happens on the held transaction.
pub async fn attach_by_name_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    execution_id: &str,
    project_id: &str,
    name: &str,
) -> Result<AttachOutcome, SchedulerError> {
    let sql = format!(
        "SELECT id FROM secrets WHERE project_id = ? AND name = ? AND deleted_at IS NULL{}",
        lock_suffix(pool)
    );
    let Some(row) = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .bind(name)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("lock secret failed: {e}")))?
    else {
        return Ok(AttachOutcome::NotFound);
    };
    let secret_id: String = row.get("id");
    if insert_attachment_in_tx(tx, pool, execution_id, &secret_id).await? {
        Ok(AttachOutcome::Attached)
    } else {
        Ok(AttachOutcome::AlreadyAttached)
    }
}

/// Attach several secrets by name on the held transaction, locking rows in id order.
/// Returns the first name that is not an active secret of the project.
pub async fn attach_batch_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    execution_id: &str,
    project_id: &str,
    names: &[String],
) -> Result<Option<String>, SchedulerError> {
    let mut resolved = Vec::with_capacity(names.len());
    for name in names {
        let row = sqlx::query(&pool.prepare_query(
            "SELECT id FROM secrets WHERE project_id = ? AND name = ? AND deleted_at IS NULL",
        ))
        .bind(project_id)
        .bind(name)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("resolve secret failed: {e}")))?;
        match row {
            Some(row) => resolved.push((row.get::<String, _>("id"), name.clone())),
            None => return Ok(Some(name.clone())),
        }
    }
    resolved.sort();
    for (secret_id, name) in &resolved {
        if !lock_active_by_id_in_tx(tx, pool, secret_id).await? {
            return Ok(Some(name.clone()));
        }
        insert_attachment_in_tx(tx, pool, execution_id, secret_id).await?;
    }
    Ok(None)
}

/// Detach an active secret from an execution. False when it is not attached.
pub async fn detach(
    pool: &DbPool,
    execution_id: &str,
    project_id: &str,
    name: &str,
) -> Result<bool, SchedulerError> {
    let updated = sqlx::query(&pool.prepare_query(
        "UPDATE execution_secrets SET detached_at = CURRENT_TIMESTAMP \
         WHERE execution_id = ? AND detached_at IS NULL AND secret_id IN \
         (SELECT id FROM secrets WHERE project_id = ? AND name = ? AND deleted_at IS NULL)",
    ))
    .bind(execution_id)
    .bind(project_id)
    .bind(name)
    .execute(pool.as_ref())
    .await
    .map_err(|e| SchedulerError::Database(format!("detach secret failed: {e}")))?
    .rows_affected();
    Ok(updated > 0)
}

/// Names of the active secrets attached to an execution.
pub async fn list_attached(
    pool: &DbPool,
    execution_id: &str,
) -> Result<Vec<AttachedSecret>, SchedulerError> {
    let attached = pool.format_timestamp(TimestampColumn::AttachedAt);
    let sql = format!(
        "SELECT s.name as name, {attached} as attached_at FROM execution_secrets a \
         JOIN secrets s ON s.id = a.secret_id \
         WHERE a.execution_id = ? AND a.detached_at IS NULL AND s.deleted_at IS NULL \
         ORDER BY s.name"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(execution_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list attached secrets failed: {e}")))?;
    rows.iter()
        .map(|row| {
            Ok(AttachedSecret {
                name: row.get("name"),
                attached_at: parse_timestamp(row, "attached_at")?,
            })
        })
        .collect()
}

/// The value of `name` for a session, checked against the session's credential,
/// the liveness of the session and its execution, and the attachment, in one
/// statement.
pub async fn fetch_for_session(
    pool: &DbPool,
    session_id: &str,
    credential_hash: &str,
    name: &str,
) -> Result<FetchOutcome, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query(
        "SELECT s2.value AS value FROM credentials c \
         JOIN sessions s ON s.id = c.principal_id \
         JOIN executions e ON e.id = s.execution_id \
         LEFT JOIN secrets s2 \
                ON s2.project_id = e.project_id AND s2.name = ? AND s2.deleted_at IS NULL \
               AND EXISTS (SELECT 1 FROM execution_secrets a \
                           WHERE a.execution_id = e.id AND a.secret_id = s2.id \
                             AND a.detached_at IS NULL) \
         WHERE c.token_hash = ? AND c.principal_type = 'session' AND c.principal_id = ? \
           AND s.desired <> 'terminate' AND s.outcome IS NULL \
           AND e.desired <> 'terminate' AND e.outcome IS NULL",
    ))
    .bind(name)
    .bind(credential_hash)
    .bind(session_id)
    .fetch_optional(pool.as_ref())
    .await
    .map_err(|e| SchedulerError::Database(format!("fetch secret failed: {e}")))?;
    let Some(row) = row else {
        return Ok(FetchOutcome::NotLive);
    };
    let value: Option<String> = row.get("value");
    Ok(match value {
        Some(value) => FetchOutcome::Value(SecretValue::new(value)),
        None => FetchOutcome::NotAttached,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_value_debug_hides_the_value() {
        let printed = format!("{:?}", SecretValue::new("s3cr3t-placeholder".into()));
        assert!(!printed.contains("s3cr3t-placeholder"));
        assert_eq!(printed, "SecretValue([redacted])");
    }
}
