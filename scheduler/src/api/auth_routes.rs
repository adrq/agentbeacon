// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Login, the list of a user's logins, and sign-out.

use axum::{
    Json, Router,
    extract::{Path, State, rejection::JsonRejection},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
};
use serde::{Deserialize, Serialize};

use crate::api::authz::{Authenticated, unauthorized_with};
use crate::api::problem::{Problem, ProblemCode, invalid};
use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::services::auth::principal::Principal;
use crate::services::auth::{PASSWORD_MAX_CHARS, mint_token, password};

/// A login request; `Debug` never prints the password.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoginRequest {
    username: String,
    password: String,
    #[serde(default)]
    label: Option<String>,
    delivery: Delivery,
}

impl std::fmt::Debug for LoginRequest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LoginRequest")
            .field("username", &self.username)
            .field("password", &"[redacted]")
            .field("label", &self.label)
            .field("delivery", &self.delivery)
            .finish()
    }
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
enum Delivery {
    Cookie,
    Bearer,
}

/// One login, as shown to its owner.
#[derive(Debug, Serialize)]
pub struct LoginResponse {
    pub id: String,
    pub label: Option<String>,
    pub created_at: String,
    pub expires_at: Option<String>,
}

#[derive(Debug, Serialize)]
struct LoginListEntry {
    id: String,
    label: Option<String>,
    created_at: String,
    expires_at: Option<String>,
    current: bool,
}

fn login_response(login: db::credentials::Login) -> LoginResponse {
    LoginResponse {
        id: login.token_hash,
        label: login.label,
        created_at: login.created_at,
        expires_at: login.expires_at,
    }
}

fn internal(e: impl std::fmt::Display) -> Response {
    tracing::error!(error = %e, "login failed");
    Problem::new(ProblemCode::InternalError)
        .with_detail("the login could not be completed")
        .into_response()
}

fn login_cookie(state: &AppState, headers: &HeaderMap, value: &str, max_age: i64) -> HeaderValue {
    let cookie = cookie_value(
        &state.login_cookie_name,
        value,
        max_age,
        &state.resolve_base_url(headers),
    );
    HeaderValue::from_str(&cookie).unwrap_or_else(|_| HeaderValue::from_static(""))
}

/// The `Set-Cookie` value for a login; `Secure` when the server is reached over https.
fn cookie_value(name: &str, value: &str, max_age: i64, base_url: &str) -> String {
    let secure = if base_url.starts_with("https://") {
        "; Secure"
    } else {
        ""
    };
    format!("{name}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}")
}

/// Verify a password off the async runtime.
async fn verify(password: String, stored: Option<String>) -> bool {
    tokio::task::spawn_blocking(move || match stored {
        Some(stored) => password::verify_password(&password, &stored),
        None => {
            password::verify_against_placeholder(&password);
            false
        }
    })
    .await
    .unwrap_or(false)
}

/// Detail of every 400 for a login body that cannot be read; it never repeats
/// the body.
const LOGIN_BODY_INVALID: &str = "the request body is not a valid login request";

/// Exchange a username and password for a new login (POST /api/v1/auth/login).
async fn login(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Result<Json<LoginRequest>, JsonRejection>,
) -> Response {
    let Json(req) = match body {
        Ok(body) => body,
        Err(_) => return invalid(LOGIN_BODY_INVALID).into_response(),
    };
    if req.password.chars().count() > PASSWORD_MAX_CHARS {
        return unauthorized_login();
    }
    let user = match db::users::get_by_username(&state.db_pool, &req.username).await {
        Ok(user) => user,
        Err(e) => return internal(e),
    };
    let stored = user.as_ref().and_then(|u| u.password_hash.clone());
    let verified = verify(req.password.clone(), stored.clone()).await;
    let (Some(user), Some(stored), true) = (user, stored, verified) else {
        return unauthorized_login();
    };

    let token = mint_token();
    let pool = &state.db_pool;
    let mut tx = match pool.begin().await {
        Ok(tx) => tx,
        Err(e) => return internal(e),
    };
    // Lock the user row, then confirm the stored hash is still the verified one.
    match db::users::lock_in_tx(&mut tx, pool, &user.id).await {
        Ok(true) => {}
        Ok(false) => {
            let _ = tx.rollback().await;
            return unauthorized_login();
        }
        Err(e) => return internal(e),
    }
    match db::users::password_hash_in_tx(&mut tx, pool, &user.id).await {
        Ok(Some(current)) if current == stored => {}
        Ok(_) => {
            let _ = tx.rollback().await;
            return unauthorized_login();
        }
        Err(e) => return internal(e),
    }
    if let Err(e) = db::credentials::insert_in_tx(
        &mut tx,
        pool,
        &token.hash,
        db::credentials::PRINCIPAL_USER,
        &user.id,
        req.label.as_deref(),
        Some(state.login_ttl_secs),
    )
    .await
    {
        return internal(e);
    }
    if let Err(e) = tx.commit().await {
        return internal(e);
    }

    let login = match db::credentials::list_user_logins(pool, &user.id).await {
        Ok(logins) => logins.into_iter().find(|l| l.token_hash == token.hash),
        Err(e) => return internal(e),
    };
    let Some(login) = login else {
        return internal("the new login was not found");
    };
    let login = login_response(login);

    match req.delivery {
        Delivery::Bearer => (
            StatusCode::CREATED,
            Json(serde_json::json!({ "token": token.plaintext, "login": login })),
        )
            .into_response(),
        Delivery::Cookie => {
            let mut response = (
                StatusCode::CREATED,
                Json(serde_json::json!({ "login": login })),
            )
                .into_response();
            response.headers_mut().insert(
                header::SET_COOKIE,
                login_cookie(&state, &headers, &token.plaintext, state.login_ttl_secs),
            );
            response
        }
    }
}

fn unauthorized_login() -> Response {
    unauthorized_with("invalid username or password")
}

fn human(principal: &Principal) -> Result<(&str, &str, bool), SchedulerError> {
    match principal {
        Principal::Human {
            user_id,
            credential_hash,
            via_cookie,
        } => Ok((user_id, credential_hash, *via_cookie)),
        _ => Err(SchedulerError::Forbidden("human only".into())),
    }
}

/// The caller's logins (GET /api/v1/auth/logins).
async fn list_logins(
    State(state): State<AppState>,
    Authenticated(principal): Authenticated,
) -> Result<Json<Vec<LoginListEntry>>, SchedulerError> {
    let (user_id, current, _) = human(&principal)?;
    let logins = db::credentials::list_user_logins(&state.db_pool, user_id).await?;
    Ok(Json(
        logins
            .into_iter()
            .map(|l| LoginListEntry {
                current: l.token_hash == current,
                id: l.token_hash,
                label: l.label,
                created_at: l.created_at,
                expires_at: l.expires_at,
            })
            .collect(),
    ))
}

/// Delete one of the caller's logins (DELETE /api/v1/auth/logins/{id}).
async fn delete_login(
    State(state): State<AppState>,
    Authenticated(principal): Authenticated,
    Path(id): Path<String>,
) -> Result<StatusCode, SchedulerError> {
    let (user_id, _, _) = human(&principal)?;
    if db::credentials::delete_user_login(&state.db_pool, user_id, &id).await? {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::ResourceNotFound).with_detail("no login has this id"),
        )))
    }
}

/// Delete the login this request presented (POST /api/v1/auth/logout).
async fn logout(
    State(state): State<AppState>,
    headers: HeaderMap,
    Authenticated(principal): Authenticated,
) -> Result<Response, SchedulerError> {
    let (user_id, current, via_cookie) = human(&principal)?;
    db::credentials::delete_user_login(&state.db_pool, user_id, current).await?;
    let mut response = StatusCode::NO_CONTENT.into_response();
    if via_cookie {
        response
            .headers_mut()
            .insert(header::SET_COOKIE, login_cookie(&state, &headers, "", 0));
    }
    Ok(response)
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/auth/login", post(login))
        .route("/auth/logins", get(list_logins))
        .route("/auth/logins/{id}", delete(delete_login))
        .route("/auth/logout", post(logout))
}

#[cfg(test)]
mod tests {
    use super::{Delivery, LoginRequest, cookie_value};

    #[test]
    fn login_request_debug_omits_the_password() {
        let request = LoginRequest {
            username: "admin".to_string(),
            password: "password-secret-value".to_string(),
            label: Some("cli".to_string()),
            delivery: Delivery::Bearer,
        };
        assert_eq!(
            format!("{request:?}"),
            r#"LoginRequest { username: "admin", password: "[redacted]", label: Some("cli"), delivery: Bearer }"#
        );
    }

    #[test]
    fn login_cookie_is_secure_only_over_https() {
        assert_eq!(
            cookie_value(
                "agentbeacon_login_9456",
                "t",
                60,
                "https://beacon.example.com"
            ),
            "agentbeacon_login_9456=t; HttpOnly; SameSite=Strict; Path=/; Max-Age=60; Secure"
        );
        assert_eq!(
            cookie_value("agentbeacon_login_9456", "t", 60, "http://localhost:9456"),
            "agentbeacon_login_9456=t; HttpOnly; SameSite=Strict; Path=/; Max-Age=60"
        );
    }
}
