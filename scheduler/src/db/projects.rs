use std::sync::LazyLock;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::Row;

use super::helpers::{map_db_error, parse_timestamp};
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

/// Maximum length of a project slug.
pub const SLUG_MAX_LEN: usize = 200;

static SLUG_RE: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$").unwrap());

static UUID_RE: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    )
    .unwrap()
});

/// True when a string has the shape of a UUID.
pub fn is_uuid_shaped(value: &str) -> bool {
    UUID_RE.is_match(value)
}

/// Check a caller-supplied slug against the slug grammar and length limit.
pub fn validate_slug(slug: &str) -> Result<String, SchedulerError> {
    let normalized = slug.trim();
    if normalized.is_empty() || normalized.len() > SLUG_MAX_LEN {
        return Err(SchedulerError::ValidationFailed(format!(
            "slug must be 1-{SLUG_MAX_LEN} characters"
        )));
    }
    if !SLUG_RE.is_match(normalized) {
        return Err(SchedulerError::ValidationFailed(
            "slug must contain only lowercase letters, numbers, and hyphens (no leading/trailing/consecutive hyphens)".into(),
        ));
    }
    Ok(normalized.to_string())
}

/// Derive a slug from a project name, falling back to `own_id`.
///
/// Lowercases, turns every character outside `[a-z0-9]` into a separator,
/// collapses separator runs, trims, and truncates to `SLUG_MAX_LEN`. Returns
/// `own_id` when the name holds a non-ASCII character, when nothing survives,
/// or when the result has the shape of a UUID.
pub fn derive_slug(name: &str, own_id: &str) -> String {
    if !name.is_ascii() {
        return own_id.to_string();
    }

    let mut slug = String::with_capacity(name.len());
    let mut pending_separator = false;
    for ch in name.chars() {
        if ch.is_ascii_alphanumeric() {
            if pending_separator && !slug.is_empty() {
                slug.push('-');
            }
            pending_separator = false;
            slug.push(ch.to_ascii_lowercase());
        } else {
            pending_separator = true;
        }
    }

    slug.truncate(SLUG_MAX_LEN);
    let slug = slug.trim_end_matches('-');

    if slug.is_empty() || is_uuid_shaped(slug) {
        return own_id.to_string();
    }
    slug.to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub slug: String,
    pub path: String,
    pub settings: String,
    pub deleted_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub async fn create(
    pool: &DbPool,
    id: &str,
    name: &str,
    slug: &str,
    path: &str,
    settings: Option<&str>,
) -> Result<Project, SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO projects (id, name, slug, path, settings) VALUES (?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(name)
        .bind(slug)
        .bind(path)
        .bind(settings.unwrap_or("{}"))
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            let msg = e.to_string();
            if msg.contains("UNIQUE constraint failed") || msg.contains("duplicate key") {
                SchedulerError::Conflict("slug_exists".into())
            } else {
                SchedulerError::Database(format!("create project failed: {e}"))
            }
        })?;

    get_by_id(pool, id).await
}

/// Look up an active project by id, or by slug when the segment is not an id.
pub async fn resolve(pool: &DbPool, id_or_slug: &str) -> Result<Project, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, slug, path, settings, {created_fmt} as created_at, {updated_fmt} as updated_at \
         FROM projects WHERE (id = ? OR slug = ?) AND deleted_at IS NULL \
         ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END LIMIT 1"
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id_or_slug)
        .bind(id_or_slug)
        .bind(id_or_slug)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("project", id_or_slug, e))?;

    parse_project_row(row)
}

/// True when `project_id` names an active project, read on a held transaction.
pub async fn is_active_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
) -> Result<bool, SchedulerError> {
    let row = sqlx::query(
        &pool
            .prepare_query("SELECT 1 as present FROM projects WHERE id = ? AND deleted_at IS NULL"),
    )
    .bind(project_id)
    .fetch_optional(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("check project active failed: {e}")))?;
    Ok(row.is_some())
}

/// True when `project_id` is active, holding its row against a concurrent
/// soft delete for the rest of the transaction.
/// Returns the project's slug, read from the same row the lock is taken on, so
/// a caller labelling post-lock state does not need a second observation.
pub async fn lock_active_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
) -> Result<Option<String>, SchedulerError> {
    let for_share = if pool.is_postgres() { " FOR SHARE" } else { "" };
    let sql = format!("SELECT slug FROM projects WHERE id = ? AND deleted_at IS NULL{for_share}");
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("lock project failed: {e}")))?;
    Ok(row.map(|r| r.get("slug")))
}

/// True when another active project already holds this slug.
pub async fn slug_taken(
    pool: &DbPool,
    slug: &str,
    exclude_id: Option<&str>,
) -> Result<bool, SchedulerError> {
    let sql = match exclude_id {
        Some(_) => {
            "SELECT COUNT(*) as cnt FROM projects WHERE slug = ? AND deleted_at IS NULL AND id <> ?"
        }
        None => "SELECT COUNT(*) as cnt FROM projects WHERE slug = ? AND deleted_at IS NULL",
    };
    let query = pool.prepare_query(sql);

    let mut q = sqlx::query(&query).bind(slug);
    if let Some(id) = exclude_id {
        q = q.bind(id);
    }

    let row = q
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("check project slug failed: {e}")))?;

    Ok(row.get::<i64, _>("cnt") > 0)
}

/// Give every slugless project a slug, returning how many rows were filled.
pub async fn backfill_slugs(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
) -> Result<usize, SchedulerError> {
    let rows = sqlx::query(&pool.prepare_query("SELECT id, name, slug FROM projects"))
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("load projects for backfill failed: {e}")))?;

    let mut taken: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut pending: Vec<(String, String)> = Vec::new();
    for row in &rows {
        let id: String = row.get("id");
        let slug: Option<String> = row.get("slug");
        match slug {
            Some(slug) => {
                taken.insert(slug);
            }
            None => {
                let name: String = row.get("name");
                pending.push((id.clone(), derive_slug(&name, &id)));
            }
        }
    }

    let mut counts: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    for (_, candidate) in &pending {
        *counts.entry(candidate.as_str()).or_insert(0) += 1;
    }

    let mut filled = 0usize;
    for (id, candidate) in &pending {
        let unique =
            counts.get(candidate.as_str()).copied().unwrap_or(0) == 1 && !taken.contains(candidate);
        let slug = if unique {
            candidate.clone()
        } else {
            id.clone()
        };
        taken.insert(slug.clone());

        sqlx::query(&pool.prepare_query("UPDATE projects SET slug = ? WHERE id = ?"))
            .bind(&slug)
            .bind(id)
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("backfill project slug failed: {e}")))?;
        filled += 1;
    }

    Ok(filled)
}

/// Ids of every active project.
pub async fn list_active_ids(pool: &DbPool) -> Result<Vec<String>, SchedulerError> {
    let query = pool.prepare_query("SELECT id FROM projects WHERE deleted_at IS NULL");
    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list active project ids failed: {e}")))?;
    Ok(rows.iter().map(|r| r.get("id")).collect())
}

/// Ids of every active project, read on a held transaction.
pub async fn active_ids_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
) -> Result<Vec<String>, SchedulerError> {
    let rows = sqlx::query(&pool.prepare_query("SELECT id FROM projects WHERE deleted_at IS NULL"))
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list active projects failed: {e}")))?;
    Ok(rows.iter().map(|r| r.get("id")).collect())
}

/// The id of an active project addressed by id or slug, read on a transaction.
pub async fn resolve_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    id_or_slug: &str,
) -> Result<Option<String>, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query(
        "SELECT id FROM projects WHERE (id = ? OR slug = ?) AND deleted_at IS NULL \
         ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END LIMIT 1",
    ))
    .bind(id_or_slug)
    .bind(id_or_slug)
    .bind(id_or_slug)
    .fetch_optional(&mut **tx)
    .await
    .map_err(|e| SchedulerError::Database(format!("resolve project failed: {e}")))?;
    Ok(row.map(|r| r.get("id")))
}

/// `(id, slug)` for every active project.
/// `list_active_slugs`, read on a held snapshot.
pub async fn active_slugs_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
) -> Result<std::collections::HashMap<String, String>, SchedulerError> {
    let rows =
        sqlx::query(&pool.prepare_query("SELECT id, slug FROM projects WHERE deleted_at IS NULL"))
            .fetch_all(&mut **tx)
            .await
            .map_err(|e| {
                SchedulerError::Database(format!("list active project slugs failed: {e}"))
            })?;
    Ok(rows
        .iter()
        .map(|r| {
            let id: String = r.get("id");
            let slug: Option<String> = r.get("slug");
            let slug = slug.unwrap_or_else(|| id.clone());
            (id, slug)
        })
        .collect())
}

pub async fn list_active_slugs(pool: &DbPool) -> Result<Vec<(String, String)>, SchedulerError> {
    let query = pool.prepare_query("SELECT id, slug FROM projects WHERE deleted_at IS NULL");
    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list active project slugs failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| {
            let id: String = r.get("id");
            let slug: Option<String> = r.get("slug");
            let slug = slug.unwrap_or_else(|| id.clone());
            (id, slug)
        })
        .collect())
}

pub async fn get_by_id(pool: &DbPool, id: &str) -> Result<Project, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, slug, path, settings, {} as created_at, {} as updated_at FROM projects WHERE id = ? AND deleted_at IS NULL",
        created_fmt, updated_fmt
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(id)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| map_db_error("project", id, e))?;

    parse_project_row(row)
}

pub async fn list(pool: &DbPool) -> Result<Vec<Project>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, name, slug, path, settings, {} as created_at, {} as updated_at FROM projects WHERE deleted_at IS NULL ORDER BY created_at DESC",
        created_fmt, updated_fmt
    );

    let rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list projects failed: {e}")))?;

    rows.into_iter().map(parse_project_row).collect()
}

pub async fn update(
    pool: &DbPool,
    id: &str,
    name: Option<&str>,
    path: Option<&str>,
    settings: Option<&str>,
    slug: Option<&str>,
) -> Result<Project, SchedulerError> {
    let mut set_clauses = Vec::new();
    let mut bind_values: Vec<Option<String>> = Vec::new();

    if let Some(v) = name {
        set_clauses.push("name = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }
    if let Some(v) = slug {
        set_clauses.push("slug = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }
    if let Some(v) = path {
        set_clauses.push("path = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }
    if let Some(v) = settings {
        set_clauses.push("settings = ?".to_string());
        bind_values.push(Some(v.to_string()));
    }

    set_clauses.push("updated_at = CURRENT_TIMESTAMP".to_string());

    let sql = format!(
        "UPDATE projects SET {} WHERE id = ? AND deleted_at IS NULL",
        set_clauses.join(", ")
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);

    for v in bind_values.iter().flatten() {
        q = q.bind(v);
    }
    q = q.bind(id);

    let result = q.execute(pool.as_ref()).await.map_err(|e| {
        let msg = e.to_string();
        if msg.contains("UNIQUE constraint failed") || msg.contains("duplicate key") {
            SchedulerError::Conflict("slug_exists".into())
        } else {
            SchedulerError::Database(format!("update project failed: {e}"))
        }
    })?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("project not found: {id}")));
    }

    get_by_id(pool, id).await
}

pub async fn soft_delete(pool: &DbPool, id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
    );

    let result = sqlx::query(&query)
        .bind(id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("soft delete project failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!("project not found: {id}")));
    }

    Ok(())
}

pub async fn count_by_path(pool: &DbPool, path: &str) -> Result<i64, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT COUNT(*) as cnt FROM projects WHERE path = ? AND deleted_at IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(path)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("count projects by path failed: {e}")))?;

    Ok(row.get::<i64, _>("cnt"))
}

fn parse_project_row(row: sqlx::any::AnyRow) -> Result<Project, SchedulerError> {
    let id: String = row.get("id");
    let slug: Option<String> = row.get("slug");
    Ok(Project {
        slug: slug.unwrap_or_else(|| id.clone()),
        id,
        name: row.get("name"),
        path: row.get("path"),
        settings: row.get("settings"),
        deleted_at: None,
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}
