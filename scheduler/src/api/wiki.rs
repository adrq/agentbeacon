use std::collections::HashMap;
use std::sync::LazyLock;

use axum::{
    Json, Router,
    extract::{Path as AxumPath, Query, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{delete, get},
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::api::wiki_auth::{
    Access, Caller, SharingContext, resolve_caller, resolve_session_principal,
};
use crate::app::AppState;
use crate::db;
use crate::db::DbPool;
use crate::db::wiki::{
    BodyEditFailure, BodyEditReason, ForeignWriter, PageChange, PageChangeOutcome, WikiPage,
};
use crate::error::SchedulerError;
use crate::search::SearchScope;

/// Deny codes for callers that can see a page but lack the capability asked for.
const ERR_CROSS_PROJECT_COLLECTION: &str = "cross_project_collection";
const ERR_CROSS_PROJECT_CREATE: &str = "cross_project_create";
const ERR_READ_ONLY_MEMBER: &str = "read_only_member";
const ERR_NOT_PAGE_OWNER: &str = "not_page_owner";
const ERR_CROSS_PROJECT_WRITES_DISABLED: &str = "cross_project_writes_disabled";

/// Message used by every wiki page 404.
const PAGE_NOT_FOUND: &str = "wiki page not found";

/// Notice carried by the publication confirmation.
const HISTORY_WARNING: &str = "all history travels with the page";

const MAX_EDITS: usize = 50;

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
#[serde(deny_unknown_fields)]
struct PutPageRequest {
    title: String,
    body: String,
    #[serde(rename = "summary")]
    _summary: Option<String>,
    tags: Option<Vec<String>>,
    acknowledge_share: Option<bool>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchEdit {
    old_string: String,
    new_string: String,
    #[serde(default)]
    replace_all: bool,
}

/// The rename interlock.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchTitle {
    old: String,
    new: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchPageRequest {
    revision_number: i64,
    edits: Option<Vec<PatchEdit>>,
    title: Option<PatchTitle>,
    add_tags: Option<Vec<String>>,
    remove_tags: Option<Vec<String>>,
    acknowledge_share: Option<bool>,
    summary: Option<String>,
}

fn apply_edits(body: &str, edits: &[PatchEdit]) -> Result<String, BodyEditFailure> {
    let mut current = body.to_string();
    for (i, edit) in edits.iter().enumerate() {
        if edit.replace_all {
            if !current.contains(&edit.old_string) {
                return Err(BodyEditFailure {
                    index: Some(i),
                    reason: BodyEditReason::NotFound,
                });
            }
            current = current.replace(&edit.old_string, &edit.new_string);
        } else {
            match current.find(&edit.old_string) {
                None => {
                    return Err(BodyEditFailure {
                        index: Some(i),
                        reason: BodyEditReason::NotFound,
                    });
                }
                Some(pos) => {
                    if current
                        .as_bytes()
                        .get(pos + 1..)
                        .and_then(|slice| memchr::memmem::find(slice, edit.old_string.as_bytes()))
                        .is_some()
                    {
                        return Err(BodyEditFailure {
                            index: Some(i),
                            reason: BodyEditReason::MultipleMatches,
                        });
                    }
                    let rest_start = pos + edit.old_string.len();
                    current = format!(
                        "{}{}{}",
                        &current[..pos],
                        edit.new_string,
                        &current[rest_start..]
                    );
                }
            }
        }
    }
    if current.is_empty() {
        return Err(BodyEditFailure {
            index: None,
            reason: BodyEditReason::EmptyResult,
        });
    }

    Ok(current)
}

#[derive(Serialize)]
struct WikiPageResponse {
    id: String,
    project_id: String,
    project_slug: String,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    access: Option<&'static str>,
}

impl WikiPageResponse {
    fn build(p: WikiPage, project_slug: String, tags: Vec<String>, access: Option<Access>) -> Self {
        Self {
            id: p.id,
            project_id: p.project_id,
            project_slug,
            slug: p.slug,
            title: p.title,
            body: p.body,
            revision_number: p.revision_number,
            created_by: p.created_by,
            updated_by: p.updated_by,
            created_at: p.created_at.to_rfc3339(),
            updated_at: p.updated_at.to_rfc3339(),
            tags,
            access: access.map(Access::as_str),
        }
    }
}

#[derive(Serialize)]
struct WikiPageListItem {
    page_id: String,
    project_id: String,
    project_slug: String,
    slug: String,
    title: String,
    revision_number: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    updated_by: Option<String>,
    updated_at: String,
    tags: Vec<String>,
    access: &'static str,
}

#[derive(Serialize)]
struct WikiSearchItem {
    page_id: String,
    project_id: String,
    project_slug: String,
    slug: String,
    title: String,
    revision_number: i64,
    updated_by: Option<String>,
    updated_at: String,
    tags: Vec<String>,
    score: f32,
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

#[derive(Serialize)]
struct TagResponse {
    name: String,
    tag_id: String,
    shared: bool,
    page_count: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
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
    project_id: String,
    project_slug: String,
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

#[derive(Deserialize)]
struct SearchQuery {
    q: Option<String>,
    project: Option<String>,
    limit: Option<String>,
    offset: Option<String>,
}

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

fn page_not_found() -> SchedulerError {
    SchedulerError::NotFound(PAGE_NOT_FOUND.into())
}

fn deny(status: StatusCode, code: &str) -> Response {
    (status, Json(json!({ "error": code }))).into_response()
}

/// The denial for a collection addressed at someone else's project, if any.
fn collection_denial(caller: &Caller, project_id: &str) -> Option<Response> {
    (!caller.owns(project_id)).then(|| deny(StatusCode::FORBIDDEN, ERR_CROSS_PROJECT_COLLECTION))
}

struct ResolvedPage {
    page: WikiPage,
    tags: Vec<String>,
    access: Access,
    /// The owning project's slug, read in the same snapshot as the page.
    project_slug: String,
}

/// Load a page the caller is entitled to read, or the indistinguishable 404.
async fn load_readable_page<'a>(
    pool: &'a DbPool,
    sharing: &SharingContext,
    project_id: &str,
    slug: &str,
) -> Result<(ResolvedPage, sqlx::Transaction<'a, sqlx::Any>), SchedulerError> {
    let mut read = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin read transaction failed: {e}")))?;

    if pool.is_postgres() {
        sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            .execute(&mut *read)
            .await
            .map_err(|e| SchedulerError::Database(format!("set read snapshot failed: {e}")))?;
    }

    let Some((page, tags)) =
        db::wiki::page_with_tags_in_tx(&mut read, pool, project_id, slug).await?
    else {
        return Err(page_not_found());
    };

    let snapshot = sharing.in_tx(&mut read, pool).await?;

    match snapshot.access_to(&page.project_id, &tags) {
        Some(access) => {
            let project_slug = db::projects::active_slugs_in_tx(&mut read, pool)
                .await?
                .get(&page.project_id)
                .cloned()
                .unwrap_or_else(|| page.project_id.clone());
            Ok((
                ResolvedPage {
                    page,
                    tags,
                    access,
                    project_slug,
                },
                read,
            ))
        }
        None => Err(page_not_found()),
    }
}

/// Pages the caller may see across its own project and every co-member's.
struct VisiblePage {
    page_id: String,
    project_id: String,
    slug: String,
    title: String,
    revision_number: i64,
    updated_by: Option<String>,
    updated_at: String,
    tags: Vec<String>,
    access: Access,
}

/// Open the read-only snapshot a collection response is assembled from.
async fn begin_read_snapshot(
    pool: &DbPool,
) -> Result<sqlx::Transaction<'_, sqlx::Any>, SchedulerError> {
    let mut read = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin read transaction failed: {e}")))?;
    if pool.is_postgres() {
        sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            .execute(&mut *read)
            .await
            .map_err(|e| SchedulerError::Database(format!("set read snapshot failed: {e}")))?;
    }
    Ok(read)
}

/// Pages the caller may see, read on a held snapshot.
async fn visible_pages_in_tx(
    read: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    sharing: &SharingContext,
) -> Result<Vec<VisiblePage>, SchedulerError> {
    let project_ids = sharing.reachable_project_ids();
    let pages = db::wiki::list_pages_in_projects_in_tx(read, pool, &project_ids).await?;
    let mut tags = db::wiki::page_tags_in_projects_in_tx(read, pool, &project_ids).await?;

    let mut out = Vec::new();
    for page in pages {
        let page_tags = tags.remove(&page.page_id).unwrap_or_default();
        let Some(access) = sharing.access_to(&page.project_id, &page_tags) else {
            continue;
        };
        out.push(VisiblePage {
            page_id: page.page_id,
            project_id: page.project_id,
            slug: page.slug,
            title: page.title,
            revision_number: page.revision_number,
            updated_by: page.updated_by,
            updated_at: page.updated_at.to_rfc3339(),
            tags: page_tags,
            access,
        });
    }
    Ok(out)
}

/// The 409 that asks a caller to acknowledge publication.
///
/// Lists every tag the request would publish into; `slug_conflicts` sits inside
/// each entry.
fn publication_confirmation(
    audiences: &[db::wiki::PublicationAudience],
    page_slug: &str,
) -> Response {
    let mut publishes = Vec::with_capacity(audiences.len());
    for audience in audiences {
        let mut conflicts: Vec<serde_json::Value> = audience
            .slug_conflicts
            .iter()
            .map(|(_, project_slug)| json!({ "project": project_slug, "slug": page_slug }))
            .collect();
        conflicts.sort_by_key(|c| c["project"].as_str().unwrap_or("").to_string());

        let shares_with: Vec<serde_json::Value> = audience
            .members
            .iter()
            .map(|(_, slug, access)| json!({ "project": slug, "access": access }))
            .collect();

        publishes.push(json!({
            "tag": audience.tag_name,
            "shares_with": shares_with,
            "slug_conflicts": conflicts,
        }));
    }

    (
        StatusCode::CONFLICT,
        Json(json!({
            "error": "share_tag_requires_confirmation",
            "publishes": publishes,
            "warning": HISTORY_WARNING,
            "remedy": "retry with \"acknowledge_share\": true to publish",
        })),
    )
        .into_response()
}

fn reindex(state: &AppState, page: &WikiPage, tags: &[String]) {
    if let Err(e) = state.wiki_search.index_page(page, tags) {
        tracing::warn!(error = %e, slug = %page.slug, "failed to update wiki search index");
    }
}

async fn list_pages(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(project_id): AxumPath<String>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let mut read = begin_read_snapshot(&state.db_pool).await?;
    let sharing = sharing.in_tx(&mut read, &state.db_pool).await?;
    let pages = visible_pages_in_tx(&mut read, &state.db_pool, &sharing).await?;
    let slugs = db::projects::active_slugs_in_tx(&mut read, &state.db_pool).await?;
    drop(read);

    let items: Vec<WikiPageListItem> = pages
        .into_iter()
        .map(|p| WikiPageListItem {
            project_slug: slugs
                .get(&p.project_id)
                .cloned()
                .unwrap_or_else(|| p.project_id.clone()),
            page_id: p.page_id,
            project_id: p.project_id,
            slug: p.slug,
            title: p.title,
            revision_number: p.revision_number,
            updated_by: p.updated_by,
            updated_at: p.updated_at,
            tags: p.tags,
            access: p.access.as_str(),
        })
        .collect();

    Ok(Json(items).into_response())
}

async fn get_page(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &path.project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let slug = normalize_slug(&path.slug);
    let (resolved, read) = load_readable_page(&state.db_pool, &sharing, &project.id, &slug).await?;
    drop(read);

    Ok(Json(WikiPageResponse::build(
        resolved.page,
        resolved.project_slug,
        resolved.tags,
        Some(resolved.access),
    ))
    .into_response())
}

async fn put_page(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<WikiPagePath>,
    body: Result<Json<PutPageRequest>, JsonRejection>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &path.project_id).await?;
    let slug = validate_slug(&path.slug)?;

    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if !caller.owns(&project.id) {
        return Ok(deny(StatusCode::FORBIDDEN, ERR_CROSS_PROJECT_CREATE));
    }

    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;

    if req.title.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "title must not be empty".into(),
        ));
    }

    if req.body.is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "body must not be empty".into(),
        ));
    }

    let tags = normalize_tags(req.tags.as_deref())?;

    let id = Uuid::new_v4().to_string();
    match db::wiki::create_page(
        &state.db_pool,
        &id,
        &project.id,
        &slug,
        req.title.trim(),
        &req.body,
        caller.session_id.as_deref(),
        Some(&tags),
        req.acknowledge_share.unwrap_or(false),
    )
    .await
    {
        Ok(db::wiki::CreateOutcome::NeedsConfirmation(audiences)) => {
            Ok(publication_confirmation(&audiences, &slug))
        }
        Ok(db::wiki::CreateOutcome::Created(page, page_tags, project_slug)) => {
            reindex(&state, &page, &page_tags);
            Ok((
                StatusCode::CREATED,
                Json(WikiPageResponse::build(
                    *page,
                    project_slug,
                    page_tags,
                    None,
                )),
            )
                .into_response())
        }
        Err(SchedulerError::Conflict(_)) => {
            let current = db::wiki::get_page_by_slug(&state.db_pool, &project.id, &slug).await?;
            let tags = db::wiki::list_page_tags(&state.db_pool, &current.id).await?;
            Ok((
                StatusCode::CONFLICT,
                Json(json!({
                    "error": "slug_exists",
                    "current_page": WikiPageResponse::build(current, project.slug, tags, None),
                })),
            )
                .into_response())
        }
        Err(e) => Err(e),
    }
}

fn normalize_tags(tags: Option<&[String]>) -> Result<Vec<String>, SchedulerError> {
    let Some(tags) = tags else {
        return Ok(Vec::new());
    };
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for tag in tags {
        let trimmed = tag.trim();
        if trimmed.is_empty() {
            return Err(SchedulerError::ValidationFailed(
                "tag names must not be empty".into(),
            ));
        }
        if seen.insert(trimmed.to_string()) {
            out.push(trimmed.to_string());
        }
    }
    Ok(out)
}

/// `{old, new}` title interlock, validated before anything is written.
fn parse_title(value: Option<&PatchTitle>) -> Result<Option<(String, String)>, SchedulerError> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.old.trim().is_empty() || value.new.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "title requires non-empty \"old\" and \"new\"".into(),
        ));
    }
    Ok(Some((value.old.clone(), value.new.trim().to_string())))
}

async fn patch_page(
    State(state): State<AppState>,
    AxumPath((project_id, slug)): AxumPath<(String, String)>,
    headers: HeaderMap,
    body: Result<Json<PatchPageRequest>, JsonRejection>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let slug = validate_slug(&slug)?;

    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;

    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;

    if req.revision_number < 1 {
        return Err(SchedulerError::ValidationFailed(
            "revision_number must be >= 1".into(),
        ));
    }

    let edits = req.edits.unwrap_or_default();
    let title = parse_title(req.title.as_ref())?;
    let add_tags = normalize_tags(req.add_tags.as_deref())?;
    let remove_tags = normalize_tags(req.remove_tags.as_deref())?;

    let overlap: Vec<String> = add_tags
        .iter()
        .filter(|t| remove_tags.contains(t))
        .cloned()
        .collect();
    if !overlap.is_empty() {
        return Err(SchedulerError::ValidationFailed(format!(
            "tags must not appear in both add_tags and remove_tags: {}",
            overlap.join(", ")
        )));
    }

    if edits.is_empty() && title.is_none() && add_tags.is_empty() && remove_tags.is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "patch requires edits, title, add_tags or remove_tags".into(),
        ));
    }
    if edits.len() > MAX_EDITS {
        return Err(SchedulerError::ValidationFailed(format!(
            "edits must contain at most {MAX_EDITS} items"
        )));
    }
    for (i, edit) in edits.iter().enumerate() {
        if edit.old_string.is_empty() {
            return Err(SchedulerError::ValidationFailed(format!(
                "edits[{i}].old_string must not be empty"
            )));
        }
    }

    let sharing = SharingContext::load(&state.db_pool, &caller).await?;
    let (resolved, read) = load_readable_page(&state.db_pool, &sharing, &project.id, &slug).await?;
    drop(read);

    let is_owner = caller.owns(&project.id);
    let mutates_tags = !add_tags.is_empty() || !remove_tags.is_empty();

    if !is_owner {
        if mutates_tags {
            return Ok(deny(StatusCode::FORBIDDEN, ERR_NOT_PAGE_OWNER));
        }
        if !sharing.cross_project_writes {
            return Ok(deny(
                StatusCode::FORBIDDEN,
                ERR_CROSS_PROJECT_WRITES_DISABLED,
            ));
        }
        if resolved.access != Access::ReadWrite {
            return Ok(deny(StatusCode::FORBIDDEN, ERR_READ_ONLY_MEMBER));
        }
    }

    if resolved.page.revision_number != req.revision_number {
        return Ok(revision_conflict(
            resolved.page,
            resolved.project_slug,
            resolved.tags,
        ));
    }

    let newly_added: Vec<String> = add_tags
        .iter()
        .filter(|t| !resolved.tags.contains(t))
        .cloned()
        .collect();

    let foreign = (!is_owner).then(|| ForeignWriter {
        caller_project_id: &caller.project_id,
        owner_project_id: &project.id,
    });

    let title_pair = title.as_ref().map(|(o, n)| (o.as_str(), n.as_str()));
    let change = PageChange {
        project_id: &project.id,
        slug: &slug,
        expected_page_id: &resolved.page.id,
        expected_revision: req.revision_number,
        title: title_pair,
        add_tags: &newly_added,
        remove_tags: &remove_tags,
        summary: req.summary.as_deref(),
        updated_by: caller.session_id.as_deref(),
        foreign,
        acknowledge_share: req.acknowledge_share.unwrap_or(false),
    };

    let (outcome, locked_slug) =
        db::wiki::apply_page_change(&state.db_pool, change, |body| apply_edits(body, &edits))
            .await?;
    let project_slug = locked_slug.unwrap_or(project.slug);

    match outcome {
        PageChangeOutcome::Updated(page, tags) => {
            reindex(&state, &page, &tags);
            Ok(Json(WikiPageResponse::build(
                page,
                project_slug.clone(),
                tags,
                None,
            ))
            .into_response())
        }
        PageChangeOutcome::Unchanged(page, tags) => Ok(Json(WikiPageResponse::build(
            page,
            project_slug.clone(),
            tags,
            None,
        ))
        .into_response()),
        PageChangeOutcome::RevisionConflict(page, tags) => {
            Ok(revision_conflict(page, project_slug.clone(), tags))
        }
        PageChangeOutcome::TitleMismatch(page, tags) => Ok((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({
                "error": "title_mismatch",
                "current_page": WikiPageResponse::build(page, project_slug.clone(), tags, None),
            })),
        )
            .into_response()),
        PageChangeOutcome::TagNotPresent {
            missing,
            current,
            tags,
        } => Ok((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({
                "error": "tag_not_present",
                "missing": missing,
                "current_page": WikiPageResponse::build(current, project_slug.clone(), tags, None),
            })),
        )
            .into_response()),
        PageChangeOutcome::BodyEditFailed {
            failure,
            current,
            tags,
        } => {
            let mut body = json!({
                "error": "edit_failed",
                "reason": failure.reason.as_str(),
                "current_page": WikiPageResponse::build(current, project_slug.clone(), tags, None),
            });
            if let Some(index) = failure.index {
                body["edit_index"] = json!(index);
            }
            Ok((StatusCode::UNPROCESSABLE_ENTITY, Json(body)).into_response())
        }
        PageChangeOutcome::NeedsConfirmation(audiences) => {
            Ok(publication_confirmation(&audiences, &slug))
        }
        PageChangeOutcome::AccessRevoked | PageChangeOutcome::NotFound => Err(page_not_found()),
        PageChangeOutcome::ReadOnly => Ok(deny(StatusCode::FORBIDDEN, ERR_READ_ONLY_MEMBER)),
        PageChangeOutcome::CrossProjectWritesDisabled => Ok(deny(
            StatusCode::FORBIDDEN,
            ERR_CROSS_PROJECT_WRITES_DISABLED,
        )),
        PageChangeOutcome::PrincipalGone => {
            Err(SchedulerError::Unauthorized("session is not live".into()))
        }
    }
}

fn revision_conflict(page: WikiPage, project_slug: String, tags: Vec<String>) -> Response {
    (
        StatusCode::CONFLICT,
        Json(json!({
            "error": "revision_conflict",
            "current_page": WikiPageResponse::build(page, project_slug, tags, None),
        })),
    )
        .into_response()
}

async fn delete_page(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &path.project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let slug = normalize_slug(&path.slug);
    let (resolved, read) = load_readable_page(&state.db_pool, &sharing, &project.id, &slug).await?;
    drop(read);

    if !caller.owns(&project.id) {
        return Ok(deny(StatusCode::FORBIDDEN, ERR_NOT_PAGE_OWNER));
    }

    db::wiki::delete_page(
        &state.db_pool,
        &project.id,
        &resolved.page.id,
        caller.session_id.as_deref(),
    )
    .await?;

    if let Err(e) = state.wiki_search.remove_page(&resolved.page.id) {
        tracing::warn!(error = %e, slug = %slug, "failed to remove page from wiki search index");
    }

    Ok(StatusCode::NO_CONTENT.into_response())
}

async fn list_revisions(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<WikiPagePath>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &path.project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let slug = normalize_slug(&path.slug);
    let (resolved, mut read) =
        load_readable_page(&state.db_pool, &sharing, &project.id, &slug).await?;

    let summaries =
        db::wiki::list_revisions_in_tx(&mut read, &state.db_pool, &resolved.page.id).await?;
    drop(read);

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
            .collect::<Vec<_>>(),
    )
    .into_response())
}

async fn get_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<WikiRevisionPath>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &path.project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let slug = normalize_slug(&path.slug);
    let (resolved, mut read) =
        load_readable_page(&state.db_pool, &sharing, &project.id, &slug).await?;

    let revision =
        db::wiki::get_revision_in_tx(&mut read, &state.db_pool, &resolved.page.id, path.rev)
            .await?
            .ok_or_else(|| {
                SchedulerError::NotFound(format!("wiki revision not found: {}", path.rev))
            })?;
    drop(read);

    Ok(Json(WikiRevisionResponse {
        revision_number: revision.revision_number,
        title: revision.title,
        body: revision.body,
        summary: revision.summary,
        created_by: revision.created_by,
        created_at: revision.created_at.to_rfc3339(),
    })
    .into_response())
}

async fn list_tags(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(project_id): AxumPath<String>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let mut read = begin_read_snapshot(&state.db_pool).await?;
    let sharing = sharing.in_tx(&mut read, &state.db_pool).await?;
    let pages = visible_pages_in_tx(&mut read, &state.db_pool, &sharing).await?;

    let mut counts: HashMap<String, i64> = HashMap::new();
    for page in &pages {
        for tag in &page.tags {
            *counts.entry(tag.clone()).or_insert(0) += 1;
        }
    }

    let mut shared: HashMap<String, String> = HashMap::new();
    for (tag_id, tag_name, _) in
        db::wiki_sharing::memberships_of_in_tx(&mut read, &state.db_pool, &caller.project_id)
            .await?
    {
        shared.insert(tag_name, tag_id);
    }

    let ids = db::wiki_sharing::tag_ids_by_name_in_tx(&mut read, &state.db_pool).await?;
    drop(read);

    let mut names: Vec<String> = counts.keys().cloned().collect();
    for name in shared.keys() {
        if !names.contains(name) {
            names.push(name.clone());
        }
    }
    names.sort();

    let items: Vec<TagResponse> = names
        .into_iter()
        .map(|name| TagResponse {
            page_count: counts.get(&name).copied().unwrap_or(0),
            shared: shared.contains_key(&name),
            tag_id: ids.get(&name).cloned().unwrap_or_default(),
            name,
        })
        .collect();

    Ok(Json(items).into_response())
}

async fn create_subscription(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(project_id): AxumPath<String>,
    body: Result<Json<SubscriptionRequest>, JsonRejection>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;

    if req.subscriber.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "subscriber must not be empty".into(),
        ));
    }

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
        &project.id,
        req.subscriber.trim(),
        page_slug.as_deref(),
        tag_name,
        caller.session_id.as_deref(),
    )
    .await?;

    let status = if was_created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };

    Ok((status, Json(subscription_response(sub))).into_response())
}

fn subscription_response(s: db::wiki::WikiSubscription) -> SubscriptionResponse {
    SubscriptionResponse {
        id: s.id,
        project_id: s.project_id,
        subscriber: s.subscriber,
        page_slug: s.page_slug,
        tag_name: s.tag_name,
        created_at: s.created_at.to_rfc3339(),
    }
}

async fn list_subscriptions(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(project_id): AxumPath<String>,
    Query(query): Query<ListSubscriptionsQuery>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    let sharing = SharingContext::load(&state.db_pool, &caller).await?;
    let mut read = begin_read_snapshot(&state.db_pool).await?;
    sharing.in_tx(&mut read, &state.db_pool).await?;
    let subs = db::wiki::list_subscriptions_in_tx(
        &mut read,
        &state.db_pool,
        &project.id,
        &query.subscriber,
    )
    .await?;
    drop(read);
    Ok(Json(
        subs.into_iter()
            .map(subscription_response)
            .collect::<Vec<_>>(),
    )
    .into_response())
}

async fn delete_subscription(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<SubscriptionPath>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &path.project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    db::wiki::delete_subscription(
        &state.db_pool,
        &project.id,
        &path.sub_id,
        caller.session_id.as_deref(),
    )
    .await?;
    Ok(StatusCode::NO_CONTENT.into_response())
}

async fn list_changes(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(project_id): AxumPath<String>,
    Query(query): Query<ChangesQuery>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    if let Some(ref since) = query.since {
        chrono::DateTime::parse_from_rfc3339(since).map_err(|_| {
            SchedulerError::ValidationFailed(format!(
                "invalid RFC3339 timestamp for 'since': {since}"
            ))
        })?;
    }

    let sharing = SharingContext::load(&state.db_pool, &caller).await?;

    let mut read = begin_read_snapshot(&state.db_pool).await?;
    let sharing = sharing.in_tx(&mut read, &state.db_pool).await?;
    let visible: std::collections::HashSet<String> =
        visible_pages_in_tx(&mut read, &state.db_pool, &sharing)
            .await?
            .into_iter()
            .map(|p| p.page_id)
            .collect();

    let limit = query.limit.unwrap_or(100).clamp(0, 1000) as usize;
    let changes = db::wiki::list_changes_in_projects_in_tx(
        &mut read,
        &state.db_pool,
        &sharing.reachable_project_ids(),
        query.since.as_deref(),
        query.execution_id.as_deref(),
    )
    .await?;
    let slugs = db::projects::active_slugs_in_tx(&mut read, &state.db_pool).await?;
    drop(read);

    let items: Vec<ChangeResponse> = changes
        .into_iter()
        .filter(|c| visible.contains(&c.page_id))
        .take(limit)
        .map(|c| ChangeResponse {
            project_slug: slugs
                .get(&c.project_id)
                .cloned()
                .unwrap_or_else(|| c.project_id.clone()),
            project_id: c.project_id,
            slug: c.slug,
            title: c.title,
            revision_number: c.revision_number,
            summary: c.summary,
            created_by: c.created_by,
            created_at: c.created_at.to_rfc3339(),
        })
        .collect();

    Ok(Json(items).into_response())
}

async fn export_pages(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(project_id): AxumPath<String>,
) -> Result<Response, SchedulerError> {
    let project = db::projects::resolve(&state.db_pool, &project_id).await?;
    let caller = resolve_caller(&headers, &state.db_pool, &project.id).await?;
    if let Some(resp) = collection_denial(&caller, &project.id) {
        return Ok(resp);
    }

    let sharing = SharingContext::load(&state.db_pool, &caller).await?;
    let mut read = begin_read_snapshot(&state.db_pool).await?;
    sharing.in_tx(&mut read, &state.db_pool).await?;
    let pages = db::wiki::export_pages_in_tx(&mut read, &state.db_pool, &project.id).await?;
    drop(read);
    Ok(Json(
        pages
            .into_iter()
            .map(|p| ExportPageResponse {
                slug: p.slug,
                title: p.title,
                body: p.body,
            })
            .collect::<Vec<_>>(),
    )
    .into_response())
}

async fn search_wiki(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<SearchQuery>,
) -> Result<Response, SchedulerError> {
    let terms = query.q.as_deref().map(str::trim).unwrap_or("");
    if terms.is_empty() {
        return Err(SchedulerError::ValidationFailed("q is required".into()));
    }

    let limit =
        parse_bounded(query.limit.as_deref(), 50, |v| (1..=100).contains(&v)).ok_or_else(|| {
            SchedulerError::ValidationFailed("limit must be between 1 and 100".into())
        })?;
    let offset = parse_bounded(query.offset.as_deref(), 0, |v| v >= 0)
        .ok_or_else(|| SchedulerError::ValidationFailed("offset must be >= 0".into()))?;

    let principal = resolve_session_principal(&headers, &state.db_pool).await?;
    let caller = principal.as_ref().map(|(session_id, project_id)| Caller {
        session_id: Some(session_id.clone()),
        project_id: project_id.clone(),
    });
    let entry_context = match &caller {
        Some(caller) => Some(SharingContext::load(&state.db_pool, caller).await?),
        None => None,
    };

    let mut read = begin_read_snapshot(&state.db_pool).await?;

    let scope = match entry_context {
        Some(context) => context
            .in_tx(&mut read, &state.db_pool)
            .await?
            .search_scope(),
        None => SearchScope {
            projects: db::projects::active_ids_in_tx(&mut read, &state.db_pool).await?,
            tagged: Vec::new(),
        },
    };

    let scope = match query
        .project
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        Some(filter) => {
            let project = db::projects::resolve_in_tx(&mut read, &state.db_pool, filter)
                .await?
                .ok_or_else(|| SchedulerError::NotFound(format!("project not found: {filter}")))?;
            scope.narrowed_to(&project)
        }
        None => scope,
    };

    let slugs = db::projects::active_slugs_in_tx(&mut read, &state.db_pool).await?;
    drop(read);

    let results = state
        .wiki_search
        .search(&scope, terms, limit as usize, offset as usize)?;

    let items: Vec<WikiSearchItem> = results
        .into_iter()
        .map(|r| WikiSearchItem {
            project_slug: slugs
                .get(&r.project_id)
                .cloned()
                .unwrap_or_else(|| r.project_id.clone()),
            page_id: r.page_id,
            project_id: r.project_id,
            slug: r.slug,
            title: r.title,
            revision_number: r.revision_number,
            updated_by: r.updated_by,
            updated_at: r.updated_at,
            tags: r.tags,
            score: r.score,
        })
        .collect();

    Ok(Json(items).into_response())
}

/// Parse an optional integer parameter, rejecting anything outside `accept`.
fn parse_bounded(raw: Option<&str>, default: i64, accept: impl Fn(i64) -> bool) -> Option<i64> {
    match raw {
        None => Some(default),
        Some(value) => {
            let parsed = value.trim().parse::<i64>().ok()?;
            accept(parsed).then_some(parsed)
        }
    }
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/wiki/search", get(search_wiki))
        .route("/api/projects/{project_id}/wiki/pages", get(list_pages))
        .route(
            "/api/projects/{project_id}/wiki/pages/{slug}",
            get(get_page)
                .put(put_page)
                .patch(patch_page)
                .delete(delete_page),
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
