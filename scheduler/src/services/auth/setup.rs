// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! The `--setup` user step: create the single user or change its password.

use std::io::{BufRead, IsTerminal, Write};

use crate::db::{self, DbPool};

use super::password::{hash_password, verify_password};
use super::{validate_password, validate_username};

pub const USERNAME_ENV: &str = "AGENTBEACON_USERNAME";
pub const PASSWORD_ENV: &str = "AGENTBEACON_PASSWORD";

/// What the user step did.
#[derive(Debug, PartialEq)]
pub enum SetupOutcome {
    Created,
    PasswordChanged,
    Unchanged,
    Skipped,
}

/// How the step may interact with the person running it.
pub struct SetupMode {
    /// Prompts are allowed (stdin is a terminal).
    pub interactive: bool,
    /// Offer a password change when a user exists and no credentials were supplied.
    pub offer_password_change: bool,
}

impl SetupMode {
    pub fn detect(offer_password_change: bool) -> Self {
        Self {
            interactive: std::io::stdin().is_terminal(),
            offer_password_change,
        }
    }
}

/// Credentials supplied through the environment, if both variables are set.
pub fn credentials_from_env() -> Result<Option<(String, String)>, String> {
    let username = std::env::var(USERNAME_ENV).ok();
    let password = std::env::var(PASSWORD_ENV).ok();
    match (username, password) {
        (Some(u), Some(p)) => Ok(Some((u, p))),
        (None, None) => Ok(None),
        _ => Err(format!(
            "set both {USERNAME_ENV} and {PASSWORD_ENV}, or neither"
        )),
    }
}

/// Run the user step against `pool`.
pub async fn run(
    pool: &DbPool,
    supplied: Option<(String, String)>,
    mode: &SetupMode,
) -> Result<SetupOutcome, String> {
    if let Some((username, password)) = supplied {
        return apply(pool, &username, &password).await;
    }

    let existing = db::users::get_single(pool)
        .await
        .map_err(|e| e.to_string())?;
    match existing {
        Some(user) => {
            if !(mode.interactive && mode.offer_password_change) {
                return Ok(SetupOutcome::Skipped);
            }
            if !confirm(&format!(
                "User '{}' exists. Change its password? [y/N] ",
                user.username
            ))? {
                return Ok(SetupOutcome::Skipped);
            }
            let password = prompt_new_password()?;
            apply(pool, &user.username, &password).await
        }
        None => {
            if !mode.interactive {
                return Err(format!(
                    "no user exists: set {USERNAME_ENV} and {PASSWORD_ENV}, or run --setup in a terminal"
                ));
            }
            let username = prompt_line("Username: ")?;
            validate_username(&username)?;
            let password = prompt_new_password()?;
            apply(pool, &username, &password).await
        }
    }
}

async fn apply(pool: &DbPool, username: &str, password: &str) -> Result<SetupOutcome, String> {
    validate_username(username)?;
    validate_password(password)?;
    let new_hash = hash_password(password)?;

    let mut tx = pool.begin().await.map_err(|e| e.to_string())?;
    let inserted = db::users::insert_in_tx(
        &mut tx,
        pool,
        &uuid::Uuid::new_v4().to_string(),
        username,
        &new_hash,
    )
    .await
    .map_err(|e| e.to_string())?;
    if inserted {
        tx.commit().await.map_err(|e| e.to_string())?;
        return Ok(SetupOutcome::Created);
    }
    tx.rollback().await.map_err(|e| e.to_string())?;

    let mut tx = pool.begin().await.map_err(|e| e.to_string())?;
    sqlx::query("UPDATE users SET updated_at = updated_at")
        .execute(&mut *tx)
        .await
        .map_err(|e| format!("lock user failed: {e}"))?;
    let Some(user) = db::users::get_single_in_tx(&mut tx)
        .await
        .map_err(|e| e.to_string())?
    else {
        let _ = tx.rollback().await;
        return Err("the user disappeared during setup; run --setup again".to_string());
    };
    if user.username != username {
        let _ = tx.rollback().await;
        return Err(format!(
            "a user named '{}' already exists; only one user is supported",
            user.username
        ));
    }
    if user
        .password_hash
        .as_deref()
        .is_some_and(|stored| verify_password(password, stored))
    {
        let _ = tx.rollback().await;
        return Ok(SetupOutcome::Unchanged);
    }
    db::users::update_password_in_tx(&mut tx, pool, &user.id, &new_hash)
        .await
        .map_err(|e| e.to_string())?;
    db::credentials::delete_user_logins_in_tx(&mut tx, pool, &user.id)
        .await
        .map_err(|e| e.to_string())?;
    tx.commit().await.map_err(|e| e.to_string())?;
    Ok(SetupOutcome::PasswordChanged)
}

fn prompt_line(prompt: &str) -> Result<String, String> {
    print!("{prompt}");
    std::io::stdout().flush().map_err(|e| e.to_string())?;
    let mut line = String::new();
    std::io::stdin()
        .lock()
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    Ok(line.trim_end_matches(['\r', '\n']).to_string())
}

fn confirm(prompt: &str) -> Result<bool, String> {
    let answer = prompt_line(prompt)?;
    Ok(matches!(answer.trim(), "y" | "Y" | "yes" | "YES" | "Yes"))
}

fn prompt_new_password() -> Result<String, String> {
    let password = rpassword::prompt_password("Password: ").map_err(|e| e.to_string())?;
    validate_password(&password)?;
    let again = rpassword::prompt_password("Repeat password: ").map_err(|e| e.to_string())?;
    if password != again {
        return Err("the passwords do not match".to_string());
    }
    Ok(password)
}
