// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use sqlx::Row;

use crate::error::SchedulerError;

use super::DbPool;

/// A human user.
#[derive(Clone)]
pub struct User {
    pub id: String,
    pub username: String,
    pub password_hash: Option<String>,
}

fn user_from_row(row: &sqlx::any::AnyRow) -> User {
    User {
        id: row.get("id"),
        username: row.get("username"),
        password_hash: row.get("password_hash"),
    }
}

/// Insert a user. Returns `false` when a uniqueness rule refused the row.
pub async fn insert_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    id: &str,
    username: &str,
    password_hash: &str,
) -> Result<bool, SchedulerError> {
    let result = sqlx::query(
        &pool.prepare_query("INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)"),
    )
    .bind(id)
    .bind(username)
    .bind(password_hash)
    .execute(&mut **tx)
    .await;
    match result {
        Ok(_) => Ok(true),
        Err(e) => {
            let msg = e.to_string();
            if msg.contains("UNIQUE constraint failed") || msg.contains("duplicate key") {
                Ok(false)
            } else {
                Err(SchedulerError::Database(format!("insert user failed: {e}")))
            }
        }
    }
}

/// Lock the user row on a held transaction with an update that changes nothing.
pub async fn lock_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    id: &str,
) -> Result<bool, SchedulerError> {
    let result =
        sqlx::query(&pool.prepare_query("UPDATE users SET updated_at = updated_at WHERE id = ?"))
            .bind(id)
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("lock user failed: {e}")))?;
    Ok(result.rows_affected() == 1)
}

/// The single user, if one exists.
pub async fn get_single(pool: &DbPool) -> Result<Option<User>, SchedulerError> {
    let row = sqlx::query("SELECT id, username, password_hash FROM users LIMIT 1")
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("read user failed: {e}")))?;
    Ok(row.as_ref().map(user_from_row))
}

/// The single user, read on a held transaction.
pub async fn get_single_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
) -> Result<Option<User>, SchedulerError> {
    let row = sqlx::query("SELECT id, username, password_hash FROM users LIMIT 1")
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("read user failed: {e}")))?;
    Ok(row.as_ref().map(user_from_row))
}

/// The user with this username.
pub async fn get_by_username(
    pool: &DbPool,
    username: &str,
) -> Result<Option<User>, SchedulerError> {
    let row = sqlx::query(
        &pool.prepare_query("SELECT id, username, password_hash FROM users WHERE username = ?"),
    )
    .bind(username)
    .fetch_optional(pool.as_ref())
    .await
    .map_err(|e| SchedulerError::Database(format!("read user failed: {e}")))?;
    Ok(row.as_ref().map(user_from_row))
}

/// The stored password hash of a user, read on a held transaction.
pub async fn password_hash_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    id: &str,
) -> Result<Option<String>, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query("SELECT password_hash FROM users WHERE id = ?"))
        .bind(id)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("read password hash failed: {e}")))?;
    Ok(row.and_then(|r| r.get::<Option<String>, _>("password_hash")))
}

/// Replace a user's password hash.
pub async fn update_password_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    id: &str,
    password_hash: &str,
) -> Result<(), SchedulerError> {
    sqlx::query(&pool.prepare_query(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    ))
    .bind(password_hash)
    .bind(id)
    .execute(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("update password failed: {e}")))?;
    Ok(())
}
