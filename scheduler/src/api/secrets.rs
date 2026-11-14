// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::{
    Json, Router,
    extract::{
        Path, State,
        rejection::{JsonRejection, PathRejection},
    },
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use serde::{Deserialize, Serialize};

use crate::api::authz::Authenticated;
use crate::api::problem::{Problem, ProblemCode};
use crate::api::versions::SECRET_VALUE_MAX_BYTES;
use crate::app::AppState;
use crate::db;
use crate::db::secrets::{
    AttachOutcome, Attachment, CreateOutcome, DeleteOutcome, FetchOutcome, SecretMeta, SecretValue,
};
use crate::error::SchedulerError;

/// Longest secret name accepted, in bytes.
pub const SECRET_NAME_MAX_BYTES: usize = 255;

pub const NAME_RULE_DETAIL: &str = "name must match [A-Z_][A-Z0-9_]* and be at most 255 bytes";
const BODY_INVALID_DETAIL: &str = "the request body is not a valid secret request";
const BODY_TOO_LARGE_DETAIL: &str = "the request body is too large";
const VALUE_INVALID_DETAIL: &str = "value must be a non-empty string without NUL characters";
const VALUE_TOO_LARGE_DETAIL: &str = "value exceeds the size limit";
const INTERNAL_DETAIL: &str = "the secret request could not be completed";
const PROJECT_NOT_FOUND: &str = "no project has this id or slug";
const EXECUTION_NOT_FOUND: &str = "no execution has this id";
const SECRET_NOT_FOUND: &str = "no secret with this name exists in the project";
const NOT_ATTACHED: &str = "the secret is not attached to this execution";
const FETCH_NOT_FOUND: &str = "no secret with this name is attached to this execution";

/// True for an environment-variable-style name of at most 255 bytes.
pub fn valid_secret_name(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    name.len() <= SECRET_NAME_MAX_BYTES
        && (first.is_ascii_uppercase() || first == '_')
        && chars.all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
}

fn problem(code: ProblemCode, detail: &str) -> SchedulerError {
    SchedulerError::Problem(Box::new(Problem::new(code).with_detail(detail)))
}

fn invalid(detail: &str) -> SchedulerError {
    problem(ProblemCode::RequestInvalid, detail)
}

fn not_found(detail: &str) -> SchedulerError {
    problem(ProblemCode::ResourceNotFound, detail)
}

/// Convert a lookup or storage failure into a problem document: a missing row
/// becomes `resource.not_found` with `not_found_detail`, anything unexpected a
/// fixed `internal.error`.
pub fn to_problem(e: SchedulerError, not_found_detail: &str) -> SchedulerError {
    match e {
        SchedulerError::Problem(_) | SchedulerError::Unauthorized(_) => e,
        SchedulerError::NotFound(_) => not_found(not_found_detail),
        other => {
            tracing::error!(error = %other, "secret request failed");
            problem(ProblemCode::InternalError, INTERNAL_DETAIL)
        }
    }
}

/// `to_problem` for failures that have no not-found case.
pub fn internal(e: SchedulerError) -> SchedulerError {
    to_problem(e, INTERNAL_DETAIL)
}

fn body<T>(body: Result<Json<T>, JsonRejection>) -> Result<T, SchedulerError> {
    match body {
        Ok(Json(body)) => Ok(body),
        Err(rejection) if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE => {
            Err(problem(ProblemCode::PayloadTooLarge, BODY_TOO_LARGE_DETAIL))
        }
        Err(_) => Err(invalid(BODY_INVALID_DETAIL)),
    }
}

fn check_value(value: &str) -> Result<(), SchedulerError> {
    if value.is_empty() || value.contains('\0') {
        return Err(invalid(VALUE_INVALID_DETAIL));
    }
    if value.len() > SECRET_VALUE_MAX_BYTES {
        return Err(problem(
            ProblemCode::PayloadTooLarge,
            VALUE_TOO_LARGE_DETAIL,
        ));
    }
    Ok(())
}

/// A `{name}` path segment that decodes and follows the name rule, else the
/// route's fixed not-found response.
fn path_name(name: Option<String>, detail: &str) -> Result<String, SchedulerError> {
    match name {
        Some(name) if valid_secret_name(&name) => Ok(name),
        _ => Err(not_found(detail)),
    }
}

async fn resolve_project(state: &AppState, id: &str) -> Result<String, SchedulerError> {
    db::projects::resolve(&state.db_pool, id)
        .await
        .map(|p| p.id)
        .map_err(|e| to_problem(e, PROJECT_NOT_FOUND))
}

/// Validate the secret names of a launch request: deduplicated, each a valid name
/// of an active secret in the project. Errors are `request.invalid`.
pub async fn launch_secret_names(
    pool: &db::DbPool,
    project_id: Option<&str>,
    names: &[String],
) -> Result<Vec<String>, SchedulerError> {
    let mut unique: Vec<String> = Vec::with_capacity(names.len());
    for name in names {
        if !unique.contains(name) {
            unique.push(name.clone());
        }
    }
    if unique.is_empty() {
        return Ok(unique);
    }
    let Some(project_id) = project_id else {
        return Err(invalid("secrets require a project_id"));
    };
    for name in &unique {
        if !valid_secret_name(name) {
            return Err(invalid(NAME_RULE_DETAIL));
        }
        if db::secrets::get_active(pool, project_id, name)
            .await
            .map_err(internal)?
            .is_none()
        {
            return Err(invalid(&format!("secret not found in project: {name}")));
        }
    }
    Ok(unique)
}

#[derive(Debug, Serialize)]
pub struct AttachedExecutionResponse {
    pub execution_id: String,
    pub attached_at: String,
}

#[derive(Debug, Serialize)]
pub struct ProjectSecretResponse {
    pub name: String,
    pub created_at: String,
    pub updated_at: String,
    pub attached_executions: Vec<AttachedExecutionResponse>,
}

impl ProjectSecretResponse {
    fn new(meta: SecretMeta, attachments: Vec<Attachment>) -> Self {
        Self {
            name: meta.name,
            created_at: meta.created_at.to_rfc3339(),
            updated_at: meta.updated_at.to_rfc3339(),
            attached_executions: attachments
                .into_iter()
                .map(|a| AttachedExecutionResponse {
                    execution_id: a.execution_id,
                    attached_at: a.attached_at.to_rfc3339(),
                })
                .collect(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ExecutionSecretResponse {
    pub name: String,
    pub attached_at: String,
}

/// The value served to an agent; `Debug` never prints it.
#[derive(Serialize)]
struct SecretFetchResponse {
    name: String,
    value: String,
}

impl std::fmt::Debug for SecretFetchResponse {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SecretFetchResponse")
            .field("name", &self.name)
            .finish_non_exhaustive()
    }
}

/// A new secret; `Debug` never prints the value.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateSecretRequest {
    name: String,
    value: String,
}

impl std::fmt::Debug for CreateSecretRequest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CreateSecretRequest")
            .field("name", &self.name)
            .finish_non_exhaustive()
    }
}

/// A replacement value; `Debug` never prints it.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RotateSecretRequest {
    value: String,
}

impl std::fmt::Debug for RotateSecretRequest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RotateSecretRequest")
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AttachSecretRequest {
    name: String,
}

/// GET /api/v1/projects/{id}/secrets
async fn list_project_secrets(
    State(state): State<AppState>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Json<Vec<ProjectSecretResponse>>, SchedulerError> {
    let Ok(Path(id)) = path else {
        return Err(not_found(PROJECT_NOT_FOUND));
    };
    let project_id = resolve_project(&state, &id).await?;
    let entries = db::secrets::list_by_project(&state.db_pool, &project_id)
        .await
        .map_err(internal)?;
    Ok(Json(
        entries
            .into_iter()
            .map(|(meta, attachments)| ProjectSecretResponse::new(meta, attachments))
            .collect(),
    ))
}

/// POST /api/v1/projects/{id}/secrets
async fn create_project_secret(
    State(state): State<AppState>,
    path: Result<Path<String>, PathRejection>,
    req: Result<Json<CreateSecretRequest>, JsonRejection>,
) -> Result<impl IntoResponse, SchedulerError> {
    let Ok(Path(id)) = path else {
        return Err(not_found(PROJECT_NOT_FOUND));
    };
    let req = body(req)?;
    if !valid_secret_name(&req.name) {
        return Err(invalid(NAME_RULE_DETAIL));
    }
    check_value(&req.value)?;
    let project_id = resolve_project(&state, &id).await?;
    let value = SecretValue::new(req.value);
    match db::secrets::create(&state.db_pool, &project_id, &req.name, &value)
        .await
        .map_err(internal)?
    {
        CreateOutcome::Created(meta) => Ok((
            StatusCode::CREATED,
            Json(ProjectSecretResponse::new(meta, Vec::new())),
        )),
        CreateOutcome::NameTaken => Err(problem(
            ProblemCode::ResourceConflict,
            "a secret with this name already exists in the project",
        )),
    }
}

/// PATCH /api/v1/projects/{id}/secrets/{name}
async fn rotate_project_secret(
    State(state): State<AppState>,
    path: Result<Path<(String, String)>, PathRejection>,
    req: Result<Json<RotateSecretRequest>, JsonRejection>,
) -> Result<Json<ProjectSecretResponse>, SchedulerError> {
    let (id, name) = match path {
        Ok(Path((id, name))) => (id, Some(name)),
        Err(_) => (String::new(), None),
    };
    let name = path_name(name, SECRET_NOT_FOUND)?;
    let req = body(req)?;
    check_value(&req.value)?;
    let project_id = resolve_project(&state, &id).await?;
    let value = SecretValue::new(req.value);
    let Some(meta) = db::secrets::rotate(&state.db_pool, &project_id, &name, &value)
        .await
        .map_err(internal)?
    else {
        return Err(not_found(SECRET_NOT_FOUND));
    };
    let attachments = db::secrets::live_attachments(&state.db_pool, &meta.id)
        .await
        .map_err(internal)?;
    Ok(Json(ProjectSecretResponse::new(meta, attachments)))
}

/// DELETE /api/v1/projects/{id}/secrets/{name}
async fn delete_project_secret(
    State(state): State<AppState>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<StatusCode, SchedulerError> {
    let (id, name) = match path {
        Ok(Path((id, name))) => (id, Some(name)),
        Err(_) => (String::new(), None),
    };
    let name = path_name(name, SECRET_NOT_FOUND)?;
    let project_id = resolve_project(&state, &id).await?;
    match db::secrets::delete(&state.db_pool, &project_id, &name)
        .await
        .map_err(internal)?
    {
        DeleteOutcome::Deleted => Ok(StatusCode::NO_CONTENT),
        DeleteOutcome::NotFound => Err(not_found(SECRET_NOT_FOUND)),
        DeleteOutcome::AttachedToLive(executions) => Err(SchedulerError::Problem(Box::new(
            Problem::new(ProblemCode::ResourceConflict)
                .with_detail("the secret is attached to a running execution; detach it first")
                .with_extension("executions", executions),
        ))),
    }
}

/// GET /api/v1/executions/{id}/secrets
async fn list_execution_secrets(
    State(state): State<AppState>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Json<Vec<ExecutionSecretResponse>>, SchedulerError> {
    let Ok(Path(id)) = path else {
        return Err(not_found(EXECUTION_NOT_FOUND));
    };
    db::executions::get_by_id(&state.db_pool, &id)
        .await
        .map_err(|e| to_problem(e, EXECUTION_NOT_FOUND))?;
    let attached = db::secrets::list_attached(&state.db_pool, &id)
        .await
        .map_err(internal)?;
    Ok(Json(
        attached
            .into_iter()
            .map(|a| ExecutionSecretResponse {
                name: a.name,
                attached_at: a.attached_at.to_rfc3339(),
            })
            .collect(),
    ))
}

/// POST /api/v1/executions/{id}/secrets
async fn attach_execution_secret(
    State(state): State<AppState>,
    path: Result<Path<String>, PathRejection>,
    req: Result<Json<AttachSecretRequest>, JsonRejection>,
) -> Result<StatusCode, SchedulerError> {
    let Ok(Path(id)) = path else {
        return Err(not_found(EXECUTION_NOT_FOUND));
    };
    let req = body(req)?;
    if !valid_secret_name(&req.name) {
        return Err(invalid(NAME_RULE_DETAIL));
    }

    let pool = &state.db_pool;
    let mut tx = db::executions::begin_execution_tx(pool, &id)
        .await
        .map_err(|e| to_problem(e, EXECUTION_NOT_FOUND))?;
    let result = async {
        let exec = db::executions::get_in_tx(pool, &mut tx, &id)
            .await
            .map_err(|e| to_problem(e, EXECUTION_NOT_FOUND))?;
        if !db::credentials::is_live(&exec.desired, exec.outcome.as_deref()) {
            return Err(problem(
                ProblemCode::ExecutionTerminated,
                "the execution has ended; secrets cannot be attached",
            ));
        }
        let Some(project_id) = exec.project_id else {
            return Err(invalid("the execution has no project"));
        };
        match db::secrets::attach_by_name_in_tx(&mut tx, pool, &id, &project_id, &req.name)
            .await
            .map_err(internal)?
        {
            AttachOutcome::NotFound => Err(not_found(SECRET_NOT_FOUND)),
            AttachOutcome::Attached | AttachOutcome::AlreadyAttached => Ok(()),
        }
    }
    .await;
    match result {
        Ok(()) => {
            tx.commit().await.map_err(|e| {
                internal(SchedulerError::Database(format!(
                    "commit attach secret failed: {e}"
                )))
            })?;
            Ok(StatusCode::NO_CONTENT)
        }
        Err(e) => {
            let _ = tx.rollback().await;
            Err(e)
        }
    }
}

/// DELETE /api/v1/executions/{id}/secrets/{name}
async fn detach_execution_secret(
    State(state): State<AppState>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<StatusCode, SchedulerError> {
    let (id, name) = match path {
        Ok(Path((id, name))) => (id, Some(name)),
        Err(_) => (String::new(), None),
    };
    let name = path_name(name, NOT_ATTACHED)?;
    let exec = db::executions::get_by_id(&state.db_pool, &id)
        .await
        .map_err(|e| to_problem(e, EXECUTION_NOT_FOUND))?;
    let Some(project_id) = exec.project_id else {
        return Err(not_found(NOT_ATTACHED));
    };
    if db::secrets::detach(&state.db_pool, &id, &project_id, &name)
        .await
        .map_err(internal)?
    {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(not_found(NOT_ATTACHED))
    }
}

/// GET /api/v1/secrets/{name}
async fn fetch_secret(
    State(state): State<AppState>,
    Authenticated(principal): Authenticated,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, SchedulerError> {
    let Some(agent) = principal.agent() else {
        return Err(SchedulerError::Forbidden("not an agent".into()));
    };
    let name = path_name(path.ok().map(|Path(name)| name), FETCH_NOT_FOUND)?;
    match db::secrets::fetch_for_session(
        &state.db_pool,
        &agent.session_id,
        &agent.credential_hash,
        &name,
    )
    .await
    .map_err(internal)?
    {
        FetchOutcome::NotLive => Err(SchedulerError::Unauthorized("session is not live".into())),
        FetchOutcome::NotAttached => Err(not_found(FETCH_NOT_FOUND)),
        FetchOutcome::Value(value) => Ok((
            [(header::CACHE_CONTROL, "no-store")],
            Json(SecretFetchResponse {
                name,
                value: value.expose().to_string(),
            }),
        )
            .into_response()),
    }
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route(
            "/projects/{id}/secrets",
            get(list_project_secrets).post(create_project_secret),
        )
        .route(
            "/projects/{id}/secrets/{name}",
            axum::routing::patch(rotate_project_secret).delete(delete_project_secret),
        )
        .route(
            "/executions/{id}/secrets",
            get(list_execution_secrets).post(attach_execution_secret),
        )
        .route(
            "/executions/{id}/secrets/{name}",
            axum::routing::delete(detach_execution_secret),
        )
        .route("/secrets/{name}", get(fetch_secret))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_debug_hides_the_value() {
        let create = CreateSecretRequest {
            name: "GH_TOKEN".into(),
            value: "s3cr3t-placeholder".into(),
        };
        let rotate = RotateSecretRequest {
            value: "s3cr3t-placeholder".into(),
        };
        let fetched = SecretFetchResponse {
            name: "GH_TOKEN".into(),
            value: "s3cr3t-placeholder".into(),
        };
        for printed in [
            format!("{create:?}"),
            format!("{rotate:?}"),
            format!("{fetched:?}"),
        ] {
            assert!(!printed.contains("s3cr3t-placeholder"), "{printed}");
        }
    }
}
