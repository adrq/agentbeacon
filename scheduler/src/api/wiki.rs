use std::sync::LazyLock;

use axum::{
    Json, Router,
    extract::{Path as AxumPath, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::app::AppState;
use crate::db;
use crate::db::DbPool;
use crate::error::SchedulerError;

// --- Request/Response types ---

#[derive(Deserialize)]
struct WikiPagePath {
    project_id: String,
    slug: String,
}

#[derive(Deserialize)]
struct WikiRevisionPath {
    project_id: String,
    slug: String,
    rev: i64,
}

#[derive(Deserialize)]
struct PutPageRequest {
    title: String,
    body: String,
    revision_number: Option<i64>,
    summary: Option<String>,
}

#[derive(Serialize)]
struct WikiPageResponse {
    id: String,
    project_id: String,
    slug: String,
    title: String,
    body: String,
    revision_number: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    created_by: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    updated_by: Option<String>,
    created_at: String,
    updated_at: String,
}

impl WikiPageResponse {
    fn from_page(p: db::wiki::WikiPage) -> Self {
        Self {
            id: p.id,
            project_id: p.project_id,
            slug: p.slug,
            title: p.title,
            body: p.body,
            revision_number: p.revision_number,
            created_by: p.created_by,
            updated_by: p.updated_by,
            created_at: p.created_at.to_rfc3339(),
            updated_at: p.updated_at.to_rfc3339(),
        }
    }
}

#[derive(Serialize)]
struct WikiPageListItem {
    slug: String,
    title: String,
    revision_number: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    updated_by: Option<String>,
    updated_at: String,
}

#[derive(Serialize)]
struct WikiRevisionResponse {
    revision_number: i64,
    title: String,
    body: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    created_by: Option<String>,
    created_at: String,
}

#[derive(Serialize)]
struct WikiRevisionListItem {
    revision_number: i64,
    title: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    created_by: Option<String>,
    created_at: String,
}

#[derive(Deserialize)]
struct ListPagesQuery {
    q: Option<String>,
}

// --- Slug validation ---

static SLUG_RE: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$").unwrap());

fn validate_slug(slug: &str) -> Result<String, SchedulerError> {
    let normalized = slug.trim().to_lowercase();
    if normalized.is_empty() || normalized.len() > 200 {
        return Err(SchedulerError::ValidationFailed(
            "slug must be 1-200 characters".into(),
        ));
    }
    if !SLUG_RE.is_match(&normalized) {
        return Err(SchedulerError::ValidationFailed(
            "slug must contain only lowercase letters, numbers, and hyphens (no leading/trailing/consecutive hyphens)".into(),
        ));
    }
    Ok(normalized)
}

/// Normalize a slug for read operations (lowercase + trim, no regex validation).
fn normalize_slug(slug: &str) -> String {
    slug.trim().to_lowercase()
}

// --- Auth helper ---

/// Extract session_id from optional Bearer token.
/// If present, validate session -> execution -> project chain.
/// Returns Ok(Some(session_id)) if valid agent auth, Ok(None) for unauthenticated.
async fn resolve_wiki_auth(
    headers: &HeaderMap,
    db_pool: &DbPool,
    url_project_id: &str,
) -> Result<Option<String>, SchedulerError> {
    let Some(auth_header) = headers.get("authorization") else {
        return Ok(None);
    };
    let header_str = auth_header.to_str().map_err(|_| {
        SchedulerError::Unauthorized("invalid Authorization header encoding".into())
    })?;

    // RFC 7235: auth schemes are case-insensitive
    let token = if header_str.len() > 7 && header_str[..7].eq_ignore_ascii_case("bearer ") {
        &header_str[7..]
    } else {
        return Err(SchedulerError::Unauthorized(
            "invalid Authorization format, expected Bearer <token>".into(),
        ));
    };

    let session = db::sessions::get_by_id(db_pool, token)
        .await
        .map_err(|_| SchedulerError::Unauthorized("session not found".into()))?;

    // Validate session's execution belongs to this project
    let execution = db::executions::get_by_id(db_pool, &session.execution_id).await?;
    if execution.project_id.as_deref() != Some(url_project_id) {
        return Err(SchedulerError::Forbidden(
            "session does not belong to this project".into(),
        ));
    }

    Ok(Some(session.id))
}

// --- Handlers ---

async fn list_pages(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
    Query(query): Query<ListPagesQuery>,
) -> Result<Json<Vec<WikiPageListItem>>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    let pages = db::wiki::list_pages(&state.db_pool, &project_id, query.q.as_deref()).await?;

    Ok(Json(
        pages
            .into_iter()
            .map(|p| WikiPageListItem {
                slug: p.slug,
                title: p.title,
                revision_number: p.revision_number,
                updated_by: p.updated_by,
                updated_at: p.updated_at.to_rfc3339(),
            })
            .collect(),
    ))
}

async fn get_page(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<Json<WikiPageResponse>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    let slug = normalize_slug(&path.slug);
    let page = db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;
    Ok(Json(WikiPageResponse::from_page(page)))
}

async fn put_page(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<WikiPagePath>,
    Json(req): Json<PutPageRequest>,
) -> Result<Response, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    // Validate slug
    let slug = validate_slug(&path.slug)?;

    // Validate title
    if req.title.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "title must not be empty".into(),
        ));
    }

    // Validate revision_number if present
    if let Some(rev) = req.revision_number
        && rev < 1
    {
        return Err(SchedulerError::ValidationFailed(
            "revision_number must be >= 1".into(),
        ));
    }

    // Resolve optional auth
    let session_id = resolve_wiki_auth(&headers, &state.db_pool, &path.project_id).await?;

    match req.revision_number {
        None => {
            // CREATE mode
            let id = Uuid::new_v4().to_string();
            match db::wiki::create_page(
                &state.db_pool,
                &id,
                &path.project_id,
                &slug,
                req.title.trim(),
                &req.body,
                session_id.as_deref(),
            )
            .await
            {
                Ok(page) => Ok(
                    (StatusCode::CREATED, Json(WikiPageResponse::from_page(page))).into_response(),
                ),
                Err(SchedulerError::Conflict(_)) => {
                    // Slug collision — return 409 with existing page
                    let current =
                        db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;
                    Ok((
                        StatusCode::CONFLICT,
                        Json(json!({
                            "error": "slug_exists",
                            "current_page": WikiPageResponse::from_page(current),
                        })),
                    )
                        .into_response())
                }
                Err(e) => Err(e),
            }
        }
        Some(expected_rev) => {
            // UPDATE mode
            match db::wiki::update_page(
                &state.db_pool,
                &path.project_id,
                &slug,
                req.title.trim(),
                &req.body,
                expected_rev,
                session_id.as_deref(),
                req.summary.as_deref(),
            )
            .await
            {
                Ok(page) => {
                    Ok((StatusCode::OK, Json(WikiPageResponse::from_page(page))).into_response())
                }
                Err(SchedulerError::Conflict(_)) => {
                    let current =
                        db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;
                    Ok((
                        StatusCode::CONFLICT,
                        Json(json!({
                            "error": "revision_conflict",
                            "current_page": WikiPageResponse::from_page(current),
                        })),
                    )
                        .into_response())
                }
                Err(e) => Err(e),
            }
        }
    }
}

async fn delete_page(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<impl IntoResponse, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    let slug = normalize_slug(&path.slug);
    db::wiki::delete_page(&state.db_pool, &path.project_id, &slug).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn list_revisions(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<Json<Vec<WikiRevisionListItem>>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    // Get page id (must exist and not be deleted)
    let slug = normalize_slug(&path.slug);
    let page = db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;

    let summaries = db::wiki::list_revisions(&state.db_pool, &page.id).await?;

    Ok(Json(
        summaries
            .into_iter()
            .map(|r| WikiRevisionListItem {
                revision_number: r.revision_number,
                title: r.title,
                summary: r.summary,
                created_by: r.created_by,
                created_at: r.created_at.to_rfc3339(),
            })
            .collect(),
    ))
}

async fn get_revision(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<WikiRevisionPath>,
) -> Result<Json<WikiRevisionResponse>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    // Get page id (must exist and not be deleted)
    let slug = normalize_slug(&path.slug);
    let page = db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;

    let revision = db::wiki::get_revision(&state.db_pool, &page.id, path.rev).await?;

    Ok(Json(WikiRevisionResponse {
        revision_number: revision.revision_number,
        title: revision.title,
        body: revision.body,
        summary: revision.summary,
        created_by: revision.created_by,
        created_at: revision.created_at.to_rfc3339(),
    }))
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/projects/{project_id}/wiki/pages", get(list_pages))
        .route(
            "/api/projects/{project_id}/wiki/pages/{slug}",
            get(get_page).put(put_page).delete(delete_page),
        )
        .route(
            "/api/projects/{project_id}/wiki/pages/{slug}/revisions",
            get(list_revisions),
        )
        .route(
            "/api/projects/{project_id}/wiki/pages/{slug}/revisions/{rev}",
            get(get_revision),
        )
}
