// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Credential presentation, principal resolution, and the authorization checks
//! shared by the middleware and the handlers.

use std::sync::Arc;

use axum::http::HeaderMap;

use crate::db::{self, DbPool};
use crate::error::SchedulerError;
use crate::supervisor::Supervisor;

use super::hash_token;

/// An agent session acting with its own token.
#[derive(Clone)]
pub struct AgentPrincipal {
    pub session_id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub project_id: Option<String>,
    pub credential_hash: String,
}

/// The authenticated caller of a request.
#[derive(Clone)]
pub enum Principal {
    Human {
        user_id: String,
        credential_hash: String,
        via_cookie: bool,
    },
    Agent(AgentPrincipal),
    Worker {
        worker_id: String,
        credential_hash: String,
    },
}

impl Principal {
    /// The hash of the credential this request presented.
    pub fn credential_hash(&self) -> &str {
        match self {
            Principal::Human {
                credential_hash, ..
            } => credential_hash,
            Principal::Agent(agent) => &agent.credential_hash,
            Principal::Worker {
                credential_hash, ..
            } => credential_hash,
        }
    }

    pub fn agent(&self) -> Option<&AgentPrincipal> {
        match self {
            Principal::Agent(agent) => Some(agent),
            _ => None,
        }
    }
}

/// What a request presented as its credential; `Debug` never prints a token.
#[derive(Clone, PartialEq)]
pub enum Presented {
    Nothing,
    Bearer(String),
    Cookie(String),
    Malformed,
}

impl std::fmt::Debug for Presented {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Nothing => f.write_str("Nothing"),
            Self::Bearer(_) => f.write_str("Bearer([redacted])"),
            Self::Cookie(_) => f.write_str("Cookie([redacted])"),
            Self::Malformed => f.write_str("Malformed"),
        }
    }
}

/// Read the credential from the `Authorization` header, or else from the login cookie.
pub fn presented(headers: &HeaderMap, cookie_name: &str) -> Presented {
    if let Some(value) = headers.get(axum::http::header::AUTHORIZATION) {
        let Ok(text) = value.to_str() else {
            return Presented::Malformed;
        };
        if text.len() > 7 && text[..7].eq_ignore_ascii_case("bearer ") {
            let token = text[7..].trim();
            if token.is_empty() {
                return Presented::Malformed;
            }
            return Presented::Bearer(token.to_string());
        }
        return Presented::Malformed;
    }
    match cookie_value(headers, cookie_name) {
        Some(value) if !value.is_empty() => Presented::Cookie(value),
        _ => Presented::Nothing,
    }
}

/// The value of one cookie, if the request carries it.
pub fn cookie_value(headers: &HeaderMap, name: &str) -> Option<String> {
    for header in headers.get_all(axum::http::header::COOKIE) {
        let Ok(text) = header.to_str() else { continue };
        for pair in text.split(';') {
            if let Some((key, value)) = pair.trim().split_once('=')
                && key.trim() == name
            {
                return Some(value.trim().to_string());
            }
        }
    }
    None
}

/// Why a presented credential did not resolve to a principal.
#[derive(Debug)]
pub enum ResolveError {
    Unauthenticated,
    Internal(SchedulerError),
}

impl From<SchedulerError> for ResolveError {
    fn from(e: SchedulerError) -> Self {
        ResolveError::Internal(e)
    }
}

/// Resolve a presented credential. `Ok(None)` when nothing was presented.
pub async fn resolve(
    pool: &DbPool,
    supervisor: &Supervisor,
    presented: &Presented,
) -> Result<Option<Principal>, ResolveError> {
    let (token, via_cookie) = match presented {
        Presented::Nothing => return Ok(None),
        Presented::Malformed => return Err(ResolveError::Unauthenticated),
        Presented::Bearer(token) => (token, false),
        Presented::Cookie(token) => (token, true),
    };
    let credential_hash = hash_token(token);
    let Some(row) = db::credentials::resolve(pool, &credential_hash).await? else {
        return Err(ResolveError::Unauthenticated);
    };
    if row.expired {
        return Err(ResolveError::Unauthenticated);
    }
    let principal = match row.principal_type.as_str() {
        db::credentials::PRINCIPAL_USER if row.user_exists => Principal::Human {
            user_id: row.principal_id,
            credential_hash,
            via_cookie,
        },
        db::credentials::PRINCIPAL_SESSION if !via_cookie => match row.session {
            Some(binding) if binding.is_live() => Principal::Agent(AgentPrincipal {
                session_id: row.principal_id,
                execution_id: binding.execution_id,
                parent_session_id: binding.parent_session_id,
                project_id: binding.project_id,
                credential_hash,
            }),
            _ => return Err(ResolveError::Unauthenticated),
        },
        db::credentials::PRINCIPAL_WORKER if !via_cookie => {
            if !supervisor.is_registered(&row.principal_id).await {
                return Err(ResolveError::Unauthenticated);
            }
            Principal::Worker {
                worker_id: row.principal_id,
                credential_hash,
            }
        }
        _ => return Err(ResolveError::Unauthenticated),
    };
    Ok(Some(principal))
}

/// A worker request's credential and the worker it names, checked again
/// wherever the request writes or publishes.
#[derive(Clone)]
pub struct WorkerBinding {
    worker_id: String,
    credential_hash: String,
    supervisor: Arc<Supervisor>,
}

impl std::fmt::Debug for WorkerBinding {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkerBinding")
            .field("worker_id", &self.worker_id)
            .finish_non_exhaustive()
    }
}

impl WorkerBinding {
    pub fn new(worker_id: String, credential_hash: String, supervisor: Arc<Supervisor>) -> Self {
        Self {
            worker_id,
            credential_hash,
            supervisor,
        }
    }

    pub fn worker_id(&self) -> &str {
        &self.worker_id
    }

    /// True while the credential row still names this worker and its
    /// registration is held.
    pub async fn holds(&self, pool: &DbPool) -> Result<bool, SchedulerError> {
        let row = db::credentials::resolve(pool, &self.credential_hash).await?;
        self.matches(row).await
    }

    /// The same check, with the credential read on a held transaction.
    pub async fn holds_in_tx(
        &self,
        tx: &mut sqlx::Transaction<'_, sqlx::Any>,
        pool: &DbPool,
    ) -> Result<bool, SchedulerError> {
        let row = db::credentials::resolve_in_tx(tx, pool, &self.credential_hash).await?;
        self.matches(row).await
    }

    async fn matches(
        &self,
        row: Option<db::credentials::ResolvedCredential>,
    ) -> Result<bool, SchedulerError> {
        let Some(row) = row else {
            return Ok(false);
        };
        Ok(row.principal_type == db::credentials::PRINCIPAL_WORKER
            && row.principal_id == self.worker_id
            && self.supervisor.is_registered(&self.worker_id).await)
    }
}

/// True while a presented credential still resolves to the same principal.
pub async fn still_valid(
    pool: &DbPool,
    supervisor: &Supervisor,
    principal: &Principal,
) -> Result<bool, SchedulerError> {
    let Some(row) = db::credentials::resolve(pool, principal.credential_hash()).await? else {
        return Ok(false);
    };
    if row.expired {
        return Ok(false);
    }
    Ok(match principal {
        Principal::Human { user_id, .. } => {
            row.principal_type == db::credentials::PRINCIPAL_USER
                && &row.principal_id == user_id
                && row.user_exists
        }
        Principal::Agent(agent) => {
            row.principal_type == db::credentials::PRINCIPAL_SESSION
                && row.principal_id == agent.session_id
                && row.session.as_ref().is_some_and(|b| b.is_live())
        }
        Principal::Worker { worker_id, .. } => {
            row.principal_type == db::credentials::PRINCIPAL_WORKER
                && &row.principal_id == worker_id
                && supervisor.is_registered(worker_id).await
        }
    })
}

/// Whether `session_id` names a session in `execution_id`.
pub async fn session_in_execution(
    pool: &DbPool,
    session_id: &str,
    execution_id: &str,
) -> Result<bool, SchedulerError> {
    match db::sessions::get_by_id(pool, session_id).await {
        Ok(session) => Ok(session.execution_id == execution_id),
        Err(SchedulerError::NotFound(_)) => Ok(false),
        Err(e) => Err(e),
    }
}

/// The project of a live session whose credential is still `credential_hash`, read on a held transaction.
pub async fn live_agent_project_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    session_id: &str,
    credential_hash: &str,
) -> Result<Option<Option<String>>, SchedulerError> {
    db::credentials::live_session_project_in_tx(tx, pool, session_id, credential_hash).await
}

#[cfg(test)]
mod tests {
    use super::Presented;

    #[test]
    fn presented_debug_omits_the_token() {
        let bearer = format!("{:?}", Presented::Bearer("bearer-secret-value".to_string()));
        let cookie = format!("{:?}", Presented::Cookie("cookie-secret-value".to_string()));
        assert_eq!(bearer, "Bearer([redacted])");
        assert_eq!(cookie, "Cookie([redacted])");
    }
}
