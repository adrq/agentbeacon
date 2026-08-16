use axum::{
    Json, Router,
    extract::{Path as AxumPath, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::api::wiki_auth::resolve_session_principal;
use crate::app::AppState;
use crate::db;
use crate::db::DbPool;
use crate::db::wiki_sharing::{ACCESS_READ_WRITE, Member, is_access_level};
use crate::error::SchedulerError;

/// Upper bound on pages listed in a confirmation payload; the count stays exact.
const PAGE_SAMPLE_CAP: usize = 50;

#[derive(Serialize)]
struct MemberResponse {
    project_id: String,
    project_slug: String,
    access_level: String,
}

#[derive(Serialize)]
struct TagListItem {
    tag_id: String,
    tag: String,
    members: Vec<MemberResponse>,
}

#[derive(Serialize)]
struct MemberDetail {
    id: String,
    tag_id: String,
    project_id: String,
    project_slug: String,
    access_level: String,
    created_at: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AdmitMemberRequest {
    project: String,
    access_level: String,
    acknowledge_share: Option<bool>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SetAccessRequest {
    access_level: String,
    acknowledge_share: Option<bool>,
}

#[derive(Deserialize)]
struct MemberPath {
    tag_id: String,
    project: String,
}

fn member_response(member: &Member) -> MemberResponse {
    MemberResponse {
        project_id: member.project_id.clone(),
        project_slug: member.project_slug.clone(),
        access_level: member.access_level.clone(),
    }
}

fn member_detail(member: &Member, tag_id: &str) -> MemberDetail {
    MemberDetail {
        id: member.id.clone(),
        tag_id: tag_id.to_string(),
        project_id: member.project_id.clone(),
        project_slug: member.project_slug.clone(),
        access_level: member.access_level.clone(),
        created_at: member.created_at.to_rfc3339(),
    }
}

/// Reject a caller presenting a session token.
async fn require_operator(headers: &HeaderMap, pool: &DbPool) -> Result<(), Response> {
    match resolve_session_principal(headers, pool).await {
        Err(e) => Err(e.into_response()),
        Ok(Some(_)) => Err((
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "operator_scope_only" })),
        )
            .into_response()),
        Ok(None) => Ok(()),
    }
}

fn tag_not_found() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": "tag_not_found" })),
    )
        .into_response()
}

fn project_not_found() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": "project_not_found" })),
    )
        .into_response()
}

fn member_not_found() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": "member_not_found" })),
    )
        .into_response()
}

fn invalid_access_level() -> SchedulerError {
    SchedulerError::ValidationFailed("access_level must be \"read\" or \"read_write\"".into())
}

async fn list_tags(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, SchedulerError> {
    if let Err(resp) = require_operator(&headers, &state.db_pool).await {
        return Ok(resp);
    }

    let tags = db::wiki_sharing::list_tags_with_members(&state.db_pool).await?;
    let items: Vec<TagListItem> = tags
        .into_iter()
        .map(|t| TagListItem {
            tag_id: t.tag_id,
            tag: t.name,
            members: t.members.iter().map(member_response).collect(),
        })
        .collect();

    Ok(Json(items).into_response())
}

/// Pages carrying `tag_id` inside `project_ids`, grouped by owning project.
/// `(owning project, slug)` for every page `project_id` can already reach
/// through a share tag other than `exclude_tag_id`.
///
/// With `write_only`, counts only pages it can already write.
async fn exposure_by_project(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    tag_id: &str,
    project_ids: &[String],
    slugs: &std::collections::HashMap<String, String>,
    already_visible: &std::collections::HashSet<String>,
) -> Result<(i64, Vec<serde_json::Value>), SchedulerError> {
    let carried = db::wiki::pages_carrying_tag_in_tx(tx, pool, tag_id, project_ids).await?;
    let rows: Vec<(String, String, String)> = carried
        .into_iter()
        .filter(|(page_id, _, _, _)| !already_visible.contains(page_id))
        .map(|(_, project_id, slug, title)| (project_id, slug, title))
        .collect();

    let mut grouped: Vec<(String, Vec<(String, String)>)> = Vec::new();
    for (project_id, slug, title) in rows {
        match grouped.iter_mut().find(|(p, _)| *p == project_id) {
            Some((_, pages)) => pages.push((slug, title)),
            None => grouped.push((project_id, vec![(slug, title)])),
        }
    }

    grouped.sort_by_key(|(project_id, _)| {
        slugs
            .get(project_id)
            .cloned()
            .unwrap_or_else(|| project_id.clone())
    });

    let mut total = 0i64;
    let mut entries = Vec::new();
    for (project_id, mut pages) in grouped {
        pages.sort_by(|a, b| a.0.cmp(&b.0));
        total += pages.len() as i64;
        entries.push(json!({
            "project": slugs.get(&project_id).cloned().unwrap_or(project_id),
            "page_count": pages.len() as i64,
            "pages": pages
                .iter()
                .take(PAGE_SAMPLE_CAP)
                .map(|(slug, title)| json!({ "slug": slug, "title": title }))
                .collect::<Vec<_>>(),
        }));
    }

    Ok((total, entries))
}

/// The answer for a project that is already an active member of the tag.
fn already_a_member(existing: &Member, tag_id: &str, requested: &str) -> Response {
    if existing.access_level == requested {
        return (StatusCode::OK, Json(member_detail(existing, tag_id))).into_response();
    }
    (
        StatusCode::CONFLICT,
        Json(json!({
            "error": "member_exists",
            "access_level": existing.access_level,
            "remedy": "use PATCH to change an existing member's access level",
        })),
    )
        .into_response()
}

async fn admit_member(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(tag_id): AxumPath<String>,
    body: Result<Json<AdmitMemberRequest>, JsonRejection>,
) -> Result<Response, SchedulerError> {
    if let Err(resp) = require_operator(&headers, &state.db_pool).await {
        return Ok(resp);
    }

    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;
    if !is_access_level(&req.access_level) {
        return Err(invalid_access_level());
    }

    let Some(tag_name) = db::wiki_sharing::tag_name(&state.db_pool, &tag_id).await? else {
        return Ok(tag_not_found());
    };
    let Ok(joiner) = db::projects::resolve(&state.db_pool, &req.project).await else {
        return Ok(project_not_found());
    };

    if let Some(existing) =
        db::wiki_sharing::active_member(&state.db_pool, &tag_id, &joiner.id).await?
    {
        return Ok(already_a_member(&existing, &tag_id, &req.access_level));
    }

    let Some(mut tx) = db::wiki_sharing::begin_locked_on_tag(&state.db_pool, &tag_id).await? else {
        return Ok(tag_not_found());
    };

    let Some(joiner_slug) =
        db::projects::lock_active_in_tx(&mut tx, &state.db_pool, &joiner.id).await?
    else {
        tx.rollback().await.map_err(|e| {
            SchedulerError::Database(format!("rollback tag transaction failed: {e}"))
        })?;
        return Ok(project_not_found());
    };

    let members = db::wiki_sharing::active_members_in_tx(&mut tx, &state.db_pool, &tag_id).await?;

    if let Some(existing) = members.iter().find(|m| m.project_id == joiner.id) {
        let response = already_a_member(existing, &tag_id, &req.access_level);
        tx.rollback().await.map_err(|e| {
            SchedulerError::Database(format!("rollback tag transaction failed: {e}"))
        })?;
        return Ok(response);
    }

    let incumbents: Vec<Member> = members
        .into_iter()
        .filter(|m| m.project_id != joiner.id)
        .collect();

    let mut slugs: std::collections::HashMap<String, String> = incumbents
        .iter()
        .map(|m| (m.project_id.clone(), m.project_slug.clone()))
        .collect();
    slugs.insert(joiner.id.clone(), joiner_slug);

    if !incumbents.is_empty() && !req.acknowledge_share.unwrap_or(false) {
        let incumbent_ids: Vec<String> = incumbents.iter().map(|m| m.project_id.clone()).collect();
        let joiner_reaches =
            db::wiki::reachable_page_ids_in_tx(&mut tx, &state.db_pool, &joiner.id, &tag_id, false)
                .await?;
        let (inbound_total, to_joiner) = exposure_by_project(
            &mut tx,
            &state.db_pool,
            &tag_id,
            &incumbent_ids,
            &slugs,
            &joiner_reaches,
        )
        .await?;

        let mut incumbents_reach: Option<std::collections::HashSet<String>> = None;
        for incumbent in &incumbent_ids {
            let reaches = db::wiki::reachable_page_ids_in_tx(
                &mut tx,
                &state.db_pool,
                incumbent,
                &tag_id,
                false,
            )
            .await?;
            incumbents_reach = Some(match incumbents_reach {
                None => reaches,
                Some(acc) => acc.intersection(&reaches).cloned().collect(),
            });
        }
        let incumbents_reach = incumbents_reach.unwrap_or_default();
        let (outbound_total, from_joiner) = exposure_by_project(
            &mut tx,
            &state.db_pool,
            &tag_id,
            std::slice::from_ref(&joiner.id),
            &slugs,
            &incumbents_reach,
        )
        .await?;

        let (write_total, grants_write) = if req.access_level == ACCESS_READ_WRITE {
            let already_writable = db::wiki::reachable_page_ids_in_tx(
                &mut tx,
                &state.db_pool,
                &joiner.id,
                &tag_id,
                true,
            )
            .await?;
            let (total, entries) = exposure_by_project(
                &mut tx,
                &state.db_pool,
                &tag_id,
                &incumbent_ids,
                &slugs,
                &already_writable,
            )
            .await?;
            (total, Some(entries))
        } else {
            (0, None)
        };

        if inbound_total + outbound_total + write_total > 0 {
            tx.rollback().await.map_err(|e| {
                SchedulerError::Database(format!("rollback tag transaction failed: {e}"))
            })?;
            let from = from_joiner
                .into_iter()
                .next()
                .map(|entry| {
                    json!({
                        "page_count": entry["page_count"],
                        "pages": entry["pages"],
                    })
                })
                .unwrap_or_else(|| json!({ "page_count": 0, "pages": [] }));

            let mut body = json!({
                "error": "membership_requires_confirmation",
                "tag": tag_name,
                "exposes_to_joiner": to_joiner,
                "exposes_from_joiner": from,
                "remedy": "retry with \"acknowledge_share\": true to share",
            });
            if let Some(entries) = grants_write {
                body["grants_write"] = json!(entries);
            }

            return Ok((StatusCode::CONFLICT, Json(body)).into_response());
        }
    }

    let id = Uuid::new_v4().to_string();
    db::wiki_sharing::insert_member_in_tx(
        &mut tx,
        &state.db_pool,
        &id,
        &tag_id,
        &joiner.id,
        &req.access_level,
        None,
    )
    .await?;
    let created =
        db::wiki_sharing::active_member_in_tx(&mut tx, &state.db_pool, &tag_id, &joiner.id)
            .await?
            .ok_or_else(|| SchedulerError::Database("membership vanished after insert".into()))?;

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit tag membership failed: {e}")))?;

    Ok((StatusCode::CREATED, Json(member_detail(&created, &tag_id))).into_response())
}

async fn set_member_access(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<MemberPath>,
    body: Result<Json<SetAccessRequest>, JsonRejection>,
) -> Result<Response, SchedulerError> {
    if let Err(resp) = require_operator(&headers, &state.db_pool).await {
        return Ok(resp);
    }

    let Json(req) = body.map_err(|e| SchedulerError::ValidationFailed(e.body_text()))?;
    if !is_access_level(&req.access_level) {
        return Err(invalid_access_level());
    }

    let Some(tag_name) = db::wiki_sharing::tag_name(&state.db_pool, &path.tag_id).await? else {
        return Ok(tag_not_found());
    };
    let Ok(project) = db::projects::resolve(&state.db_pool, &path.project).await else {
        return Ok(project_not_found());
    };

    let Some(mut tx) = db::wiki_sharing::begin_locked_on_tag(&state.db_pool, &path.tag_id).await?
    else {
        return Ok(tag_not_found());
    };

    let Some(target_slug) =
        db::projects::lock_active_in_tx(&mut tx, &state.db_pool, &project.id).await?
    else {
        tx.rollback().await.map_err(|e| {
            SchedulerError::Database(format!("rollback tag transaction failed: {e}"))
        })?;
        return Ok(project_not_found());
    };

    let Some(existing) =
        db::wiki_sharing::active_member_in_tx(&mut tx, &state.db_pool, &path.tag_id, &project.id)
            .await?
    else {
        return Ok(member_not_found());
    };

    let widening =
        existing.access_level != ACCESS_READ_WRITE && req.access_level == ACCESS_READ_WRITE;

    if widening && !req.acknowledge_share.unwrap_or(false) {
        let other_members: Vec<Member> =
            db::wiki_sharing::active_members_in_tx(&mut tx, &state.db_pool, &path.tag_id)
                .await?
                .into_iter()
                .filter(|m| m.project_id != project.id)
                .collect();
        let mut slugs: std::collections::HashMap<String, String> = other_members
            .iter()
            .map(|m| (m.project_id.clone(), m.project_slug.clone()))
            .collect();
        slugs.insert(project.id.clone(), target_slug);
        let others: Vec<String> = other_members.into_iter().map(|m| m.project_id).collect();
        let already_writable = db::wiki::reachable_page_ids_in_tx(
            &mut tx,
            &state.db_pool,
            &project.id,
            &path.tag_id,
            true,
        )
        .await?;
        let (total, grants_write) = exposure_by_project(
            &mut tx,
            &state.db_pool,
            &path.tag_id,
            &others,
            &slugs,
            &already_writable,
        )
        .await?;

        if total > 0 {
            tx.rollback().await.map_err(|e| {
                SchedulerError::Database(format!("rollback tag transaction failed: {e}"))
            })?;
            return Ok((
                StatusCode::CONFLICT,
                Json(json!({
                    "error": "membership_requires_confirmation",
                    "tag": tag_name,
                    "grants_write": grants_write,
                    "remedy": "retry with \"acknowledge_share\": true to share",
                })),
            )
                .into_response());
        }
    }

    if !db::wiki_sharing::set_access_level_in_tx(
        &mut tx,
        &state.db_pool,
        &path.tag_id,
        &project.id,
        &req.access_level,
    )
    .await?
    {
        tx.rollback().await.map_err(|e| {
            SchedulerError::Database(format!("rollback tag transaction failed: {e}"))
        })?;
        return Ok(member_not_found());
    }

    let updated =
        db::wiki_sharing::active_member_in_tx(&mut tx, &state.db_pool, &path.tag_id, &project.id)
            .await?
            .ok_or_else(|| SchedulerError::Database("membership vanished after update".into()))?;

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit tag member update failed: {e}")))?;

    Ok(Json(member_detail(&updated, &path.tag_id)).into_response())
}

async fn revoke_member(
    State(state): State<AppState>,
    headers: HeaderMap,
    AxumPath(path): AxumPath<MemberPath>,
) -> Result<Response, SchedulerError> {
    if let Err(resp) = require_operator(&headers, &state.db_pool).await {
        return Ok(resp);
    }

    if db::wiki_sharing::tag_name(&state.db_pool, &path.tag_id)
        .await?
        .is_none()
    {
        return Ok(tag_not_found());
    }
    let Ok(project) = db::projects::resolve(&state.db_pool, &path.project).await else {
        return Ok(project_not_found());
    };

    if !db::wiki_sharing::revoke_member(&state.db_pool, &path.tag_id, &project.id).await? {
        return Ok(member_not_found());
    }

    Ok(StatusCode::NO_CONTENT.into_response())
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/wiki/tags", get(list_tags))
        .route("/api/wiki/tags/{tag_id}/members", post(admit_member))
        .route(
            "/api/wiki/tags/{tag_id}/members/{project}",
            axum::routing::patch(set_member_access).delete(revoke_member),
        )
}
