use std::collections::HashMap;
use std::sync::LazyLock;

use axum::{
    Json, Router,
    extract::{Path as AxumPath, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{delete, get},
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
    tags: Option<Vec<String>>,
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
    tags: Vec<String>,
}

impl WikiPageResponse {
    fn from_page(p: db::wiki::WikiPage, tags: Vec<String>) -> Self {
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
            tags,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    score: Option<f32>,
    tags: Vec<String>,
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

// --- H3 types ---

#[derive(Serialize)]
struct TagResponse {
    name: String,
    page_count: i64,
}

#[derive(Deserialize)]
struct SubscriptionRequest {
    subscriber: String,
    page_slug: Option<String>,
    tag_name: Option<String>,
}

#[derive(Serialize)]
struct SubscriptionResponse {
    id: String,
    project_id: String,
    subscriber: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    page_slug: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tag_name: Option<String>,
    created_at: String,
}

#[derive(Deserialize)]
struct ListSubscriptionsQuery {
    subscriber: String,
}

#[derive(Deserialize)]
struct SubscriptionPath {
    project_id: String,
    sub_id: String,
}

#[derive(Deserialize)]
struct ChangesQuery {
    since: Option<String>,
    execution_id: Option<String>,
    limit: Option<i64>,
}

#[derive(Serialize)]
struct ChangeResponse {
    slug: String,
    title: String,
    revision_number: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    created_by: Option<String>,
    created_at: String,
}

#[derive(Serialize)]
struct ExportPageResponse {
    slug: String,
    title: String,
    body: String,
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

// --- Tag sync helper ---

/// Sync page tags: delete all existing associations then insert new ones.
async fn sync_page_tags(
    pool: &DbPool,
    page_id: &str,
    tags: &[String],
) -> Result<(), SchedulerError> {
    // Trim, drop empty, deduplicate
    let mut seen = std::collections::HashSet::new();
    let clean: Vec<&str> = tags
        .iter()
        .map(|t| t.trim())
        .filter(|t| !t.is_empty() && seen.insert(*t))
        .collect();

    db::wiki::delete_page_tags(pool, page_id).await?;
    for tag_name in &clean {
        let tag_id = db::wiki::get_or_create_tag(pool, tag_name).await?;
        db::wiki::add_page_tag(pool, page_id, &tag_id).await?;
    }
    Ok(())
}

// --- Handlers ---

async fn list_pages(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
    Query(query): Query<ListPagesQuery>,
) -> Result<Json<Vec<WikiPageListItem>>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    // Both paths produce items, then batch-fetch tags
    let mut items: Vec<WikiPageListItem> = if let Some(ref q) = query.q
        && !q.trim().is_empty()
    {
        let results = state.wiki_search.search(&project_id, q.trim(), 100)?;
        results
            .into_iter()
            .map(|r| WikiPageListItem {
                slug: r.slug,
                title: r.title,
                revision_number: r.revision_number,
                updated_by: r.updated_by,
                updated_at: r.updated_at,
                score: Some(r.score),
                tags: vec![],
            })
            .collect()
    } else {
        let pages = db::wiki::list_pages(&state.db_pool, &project_id).await?;
        pages
            .into_iter()
            .map(|p| WikiPageListItem {
                slug: p.slug,
                title: p.title,
                revision_number: p.revision_number,
                updated_by: p.updated_by,
                updated_at: p.updated_at.to_rfc3339(),
                score: None,
                tags: vec![],
            })
            .collect()
    };

    // Batch-fetch tags (single query, no N+1)
    let tag_pairs = db::wiki::list_page_tags_for_project(&state.db_pool, &project_id).await?;
    let mut tag_map: HashMap<String, Vec<String>> = HashMap::new();
    for (slug, tag_name) in tag_pairs {
        tag_map.entry(slug).or_default().push(tag_name);
    }
    for item in &mut items {
        if let Some(tags) = tag_map.remove(&item.slug) {
            item.tags = tags;
        }
    }

    Ok(Json(items))
}

async fn get_page(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<Json<WikiPageResponse>, SchedulerError> {
    // Verify project exists
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    let slug = normalize_slug(&path.slug);
    let page = db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;
    let tags = db::wiki::list_page_tags(&state.db_pool, &page.id).await?;
    Ok(Json(WikiPageResponse::from_page(page, tags)))
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
                Ok(page) => {
                    if let Err(e) = state.wiki_search.index_page(&path.project_id, &page) {
                        tracing::warn!(error = %e, slug = %slug, "failed to update wiki search index");
                    }
                    if let Some(ref tags) = req.tags
                        && let Err(e) = sync_page_tags(&state.db_pool, &page.id, tags).await
                    {
                        tracing::warn!(error = %e, slug = %slug, "failed to sync wiki page tags");
                    }
                    let tags = db::wiki::list_page_tags(&state.db_pool, &page.id)
                        .await
                        .unwrap_or_default();
                    Ok((
                        StatusCode::CREATED,
                        Json(WikiPageResponse::from_page(page, tags)),
                    )
                        .into_response())
                }
                Err(SchedulerError::Conflict(_)) => {
                    // Slug collision — return 409 with existing page
                    let current =
                        db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;
                    let tags = db::wiki::list_page_tags(&state.db_pool, &current.id).await?;
                    Ok((
                        StatusCode::CONFLICT,
                        Json(json!({
                            "error": "slug_exists",
                            "current_page": WikiPageResponse::from_page(current, tags),
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
                    if let Err(e) = state.wiki_search.index_page(&path.project_id, &page) {
                        tracing::warn!(error = %e, slug = %slug, "failed to update wiki search index");
                    }
                    if let Some(ref tags) = req.tags
                        && let Err(e) = sync_page_tags(&state.db_pool, &page.id, tags).await
                    {
                        tracing::warn!(error = %e, slug = %slug, "failed to sync wiki page tags");
                    }
                    let tags = db::wiki::list_page_tags(&state.db_pool, &page.id)
                        .await
                        .unwrap_or_default();
                    Ok((
                        StatusCode::OK,
                        Json(WikiPageResponse::from_page(page, tags)),
                    )
                        .into_response())
                }
                Err(SchedulerError::Conflict(_)) => {
                    let current =
                        db::wiki::get_page_by_slug(&state.db_pool, &path.project_id, &slug).await?;
                    let tags = db::wiki::list_page_tags(&state.db_pool, &current.id).await?;
                    Ok((
                        StatusCode::CONFLICT,
                        Json(json!({
                            "error": "revision_conflict",
                            "current_page": WikiPageResponse::from_page(current, tags),
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

    // Get page_id before deleting (needed for index removal)
    let page_id = db::wiki::page_id_by_slug(&state.db_pool, &path.project_id, &slug).await?;

    db::wiki::delete_page(&state.db_pool, &path.project_id, &slug).await?;

    // Best-effort index removal
    if let Some(ref pid) = page_id
        && let Err(e) = state.wiki_search.remove_page(&path.project_id, pid)
    {
        tracing::warn!(error = %e, slug = %slug, "failed to remove page from wiki search index");
    }

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

// --- H3 handlers ---

async fn list_tags(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
) -> Result<Json<Vec<TagResponse>>, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    let tags = db::wiki::list_tags_with_counts(&state.db_pool, &project_id).await?;
    Ok(Json(
        tags.into_iter()
            .map(|t| TagResponse {
                name: t.name,
                page_count: t.page_count,
            })
            .collect(),
    ))
}

async fn create_subscription(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
    Json(req): Json<SubscriptionRequest>,
) -> Result<Response, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    // Validate subscriber non-empty
    if req.subscriber.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "subscriber must not be empty".into(),
        ));
    }

    // Validate exactly one of page_slug/tag_name, and non-empty
    let page_slug = match req
        .page_slug
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        Some(s) => Some(validate_slug(s)?),
        None => None,
    };
    let tag_name = req
        .tag_name
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty());
    match (&page_slug, tag_name) {
        (Some(_), Some(_)) | (None, None) => {
            return Err(SchedulerError::ValidationFailed(
                "exactly one of page_slug or tag_name must be provided (non-empty)".into(),
            ));
        }
        _ => {}
    }

    let id = Uuid::new_v4().to_string();
    let (sub, was_created) = db::wiki::create_subscription(
        &state.db_pool,
        &id,
        &project_id,
        req.subscriber.trim(),
        page_slug.as_deref(),
        tag_name,
    )
    .await?;

    let status = if was_created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };

    let resp = SubscriptionResponse {
        id: sub.id,
        project_id: sub.project_id,
        subscriber: sub.subscriber,
        page_slug: sub.page_slug,
        tag_name: sub.tag_name,
        created_at: sub.created_at.to_rfc3339(),
    };

    Ok((status, Json(resp)).into_response())
}

async fn list_subscriptions(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
    Query(query): Query<ListSubscriptionsQuery>,
) -> Result<Json<Vec<SubscriptionResponse>>, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    let subs = db::wiki::list_subscriptions(&state.db_pool, &project_id, &query.subscriber).await?;
    Ok(Json(
        subs.into_iter()
            .map(|s| SubscriptionResponse {
                id: s.id,
                project_id: s.project_id,
                subscriber: s.subscriber,
                page_slug: s.page_slug,
                tag_name: s.tag_name,
                created_at: s.created_at.to_rfc3339(),
            })
            .collect(),
    ))
}

async fn delete_subscription(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<SubscriptionPath>,
) -> Result<impl IntoResponse, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &path.project_id).await?;

    db::wiki::delete_subscription(&state.db_pool, &path.project_id, &path.sub_id).await?;
    Ok(StatusCode::NO_CONTENT)
}

async fn list_changes(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
    Query(query): Query<ChangesQuery>,
) -> Result<Json<Vec<ChangeResponse>>, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    if let Some(ref since) = query.since {
        chrono::DateTime::parse_from_rfc3339(since).map_err(|_| {
            SchedulerError::ValidationFailed(format!(
                "invalid RFC3339 timestamp for 'since': {since}"
            ))
        })?;
    }

    let limit = query.limit.unwrap_or(100).clamp(0, 1000);
    let changes = db::wiki::list_changes(
        &state.db_pool,
        &project_id,
        query.since.as_deref(),
        query.execution_id.as_deref(),
        limit,
    )
    .await?;

    Ok(Json(
        changes
            .into_iter()
            .map(|c| ChangeResponse {
                slug: c.slug,
                title: c.title,
                revision_number: c.revision_number,
                summary: c.summary,
                created_by: c.created_by,
                created_at: c.created_at.to_rfc3339(),
            })
            .collect(),
    ))
}

async fn export_pages(
    State(state): State<AppState>,
    AxumPath(project_id): AxumPath<String>,
) -> Result<Json<Vec<ExportPageResponse>>, SchedulerError> {
    db::projects::get_by_id(&state.db_pool, &project_id).await?;

    let pages = db::wiki::export_pages(&state.db_pool, &project_id).await?;
    Ok(Json(
        pages
            .into_iter()
            .map(|p| ExportPageResponse {
                slug: p.slug,
                title: p.title,
                body: p.body,
            })
            .collect(),
    ))
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
        .route("/api/projects/{project_id}/wiki/tags", get(list_tags))
        .route(
            "/api/projects/{project_id}/wiki/subscriptions",
            get(list_subscriptions).post(create_subscription),
        )
        .route(
            "/api/projects/{project_id}/wiki/subscriptions/{sub_id}",
            delete(delete_subscription),
        )
        .route("/api/projects/{project_id}/wiki/changes", get(list_changes))
        .route("/api/projects/{project_id}/wiki/export", get(export_pages))
}
