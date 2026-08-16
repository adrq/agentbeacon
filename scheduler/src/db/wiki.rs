use chrono::{DateTime, Utc};
use sqlx::Row;
use uuid::Uuid;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::db::wiki_sharing::CROSS_PROJECT_WRITES_FLAG;
use crate::error::SchedulerError;

/// Message shared by every wiki page 404, so a denial reads like an absence.
pub const PAGE_NOT_FOUND_MESSAGE: &str = "wiki page not found";

pub struct WikiPage {
    pub id: String,
    pub project_id: String,
    pub slug: String,
    pub title: String,
    pub body: String,
    pub revision_number: i64,
    pub created_by: Option<String>,
    pub updated_by: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub struct WikiPageListItem {
    pub slug: String,
    pub title: String,
    pub revision_number: i64,
    pub updated_by: Option<String>,
    pub updated_at: DateTime<Utc>,
}

pub struct WikiPageRevision {
    pub id: String,
    pub page_id: String,
    pub revision_number: i64,
    pub title: String,
    pub body: String,
    pub summary: Option<String>,
    pub created_by: Option<String>,
    pub created_at: DateTime<Utc>,
}

pub struct WikiRevisionSummary {
    pub revision_number: i64,
    pub title: String,
    pub summary: Option<String>,
    pub created_by: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// One tag whose association would newly expose a page, with its audience.
pub struct PublicationAudience {
    pub tag_name: String,
    /// `(project_id, project_slug, access_level)` for every other active member.
    pub members: Vec<(String, String, String)>,
    /// `(project_id, project_slug)` for members already publishing this slug
    /// INTO THIS TAG, both read under the tag lock.
    pub slug_conflicts: Vec<(String, String)>,
}

/// Tag names trimmed, emptied, deduplicated and sorted.
pub fn ordered_tag_names(tags: &[String]) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut out: Vec<String> = tags
        .iter()
        .map(|t| t.trim())
        .filter(|t| !t.is_empty() && seen.insert(t.to_string()))
        .map(|t| t.to_string())
        .collect();
    out.sort();
    out
}

/// Lock `tag_names` and report which of them would publish `owner_project_id`'s
/// page, evaluated under those locks.
/// Resolve or create every named tag, returning `(name, tag_id)` sorted by name.
pub async fn materialize_tags_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    tag_names: &[String],
) -> Result<Vec<(String, String)>, SchedulerError> {
    let for_key_share = if pool.is_postgres() {
        " FOR KEY SHARE"
    } else {
        ""
    };

    let mut out = Vec::new();
    for name in ordered_tag_names(tag_names) {
        let id = Uuid::new_v4().to_string();
        let insert_sql = if pool.is_postgres() {
            pool.prepare_query(
                "INSERT INTO wiki_tags (id, name) VALUES (?, ?) ON CONFLICT (name) DO NOTHING",
            )
        } else {
            "INSERT OR IGNORE INTO wiki_tags (id, name) VALUES (?, ?)".to_string()
        };
        sqlx::query(&insert_sql)
            .bind(&id)
            .bind(&name)
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("insert wiki tag failed: {e}")))?;

        let select_sql = format!("SELECT id FROM wiki_tags WHERE name = ?{for_key_share}");
        let row = sqlx::query(&pool.prepare_query(&select_sql))
            .bind(&name)
            .fetch_one(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("lock wiki tag failed: {e}")))?;
        out.push((name, row.get("id")));
    }
    Ok(out)
}

/// The audience each tag would publish `owner_project_id`'s page to.
pub async fn audiences_for_publication(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    owner_project_id: &str,
    page_slug: &str,
    held: &[(String, String)],
) -> Result<Vec<PublicationAudience>, SchedulerError> {
    let mut audiences = Vec::new();
    for (tag_name, tag_id) in held {
        let owner_is_member_sql = pool.prepare_query(
            "SELECT 1 as present FROM wiki_tag_members m \
             JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL \
             WHERE m.tag_id = ? AND m.project_id = ? AND m.revoked_at IS NULL",
        );
        let owner_member = sqlx::query(&owner_is_member_sql)
            .bind(tag_id)
            .bind(owner_project_id)
            .fetch_optional(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("check tag membership failed: {e}")))?;
        if owner_member.is_none() {
            continue;
        }

        let others_sql = pool.prepare_query(
            "SELECT m.project_id, p.slug, m.access_level FROM wiki_tag_members m \
             JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL \
             WHERE m.tag_id = ? AND m.project_id <> ? AND m.revoked_at IS NULL \
             ORDER BY p.slug",
        );
        let rows = sqlx::query(&others_sql)
            .bind(tag_id)
            .bind(owner_project_id)
            .fetch_all(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("read tag audience failed: {e}")))?;

        if rows.is_empty() {
            continue;
        }

        let members: Vec<(String, String, String)> = rows
            .iter()
            .map(|r| {
                let project_id: String = r.get("project_id");
                let slug: Option<String> = r.get("slug");
                (
                    project_id.clone(),
                    slug.unwrap_or(project_id),
                    r.get("access_level"),
                )
            })
            .collect();

        let member_ids: Vec<String> = members.iter().map(|(id, _, _)| id.clone()).collect();
        let slug_conflicts =
            members_publishing_slug_in_tx(tx, pool, page_slug, tag_id, &member_ids).await?;

        audiences.push(PublicationAudience {
            tag_name: tag_name.clone(),
            members,
            slug_conflicts,
        });
    }

    Ok(audiences)
}

/// The result of a create that may need publication acknowledged first.
pub enum CreateOutcome {
    Created(Box<WikiPage>, Vec<String>, String),
    NeedsConfirmation(Vec<PublicationAudience>),
}

#[allow(clippy::too_many_arguments)]
/// Read a page and its tags through a transaction.
pub async fn page_with_tags_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
    slug: &str,
) -> Result<Option<(WikiPage, Vec<String>)>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let sql = format!(
        "SELECT id, project_id, slug, title, body, revision_number, created_by, updated_by, \
         {created_fmt} as created_at, {updated_fmt} as updated_at \
         FROM wiki_pages WHERE project_id = ? AND slug = ? AND deleted_at IS NULL"
    );
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .bind(slug)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("read wiki page failed: {e}")))?;

    let Some(row) = row else { return Ok(None) };
    let page = parse_wiki_page_row(row)?;
    let tags = page_tag_names_in_tx(tx, pool, &page.id).await?;
    Ok(Some((page, tags)))
}

/// `page_with_tags_in_tx`, located by immutable id.
pub async fn page_with_tags_by_id_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    page_id: &str,
) -> Result<Option<(WikiPage, Vec<String>)>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let sql = format!(
        "SELECT id, project_id, slug, title, body, revision_number, created_by, updated_by, \
         {created_fmt} as created_at, {updated_fmt} as updated_at \
         FROM wiki_pages WHERE id = ? AND deleted_at IS NULL"
    );
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(page_id)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("read wiki page failed: {e}")))?;

    let Some(row) = row else { return Ok(None) };
    let page = parse_wiki_page_row(row)?;
    let tags = page_tag_names_in_tx(tx, pool, &page.id).await?;
    Ok(Some((page, tags)))
}

#[allow(clippy::too_many_arguments)]
pub async fn create_page(
    pool: &DbPool,
    id: &str,
    project_id: &str,
    slug: &str,
    title: &str,
    body: &str,
    created_by: Option<&str>,
    tags: Option<&[String]>,
    acknowledge_share: bool,
) -> Result<CreateOutcome, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let held_tags = match tags {
        Some(tags) => materialize_tags_in_tx(&mut tx, pool, tags).await?,
        None => Vec::new(),
    };

    if tags.is_some() && !acknowledge_share {
        let audiences =
            audiences_for_publication(&mut tx, pool, project_id, slug, &held_tags).await?;
        if !audiences.is_empty() {
            if page_id_in_tx(&mut tx, pool, project_id, slug)
                .await?
                .is_some()
            {
                rollback(tx).await?;
                return Err(SchedulerError::Conflict(format!(
                    "wiki page slug already exists: {slug}"
                )));
            }
            rollback(tx).await?;
            return Ok(CreateOutcome::NeedsConfirmation(audiences));
        }
    }

    let Some(locked_project_slug) =
        crate::db::projects::lock_active_in_tx(&mut tx, pool, project_id).await?
    else {
        rollback(tx).await?;
        return Err(SchedulerError::NotFound(format!(
            "project not found: {project_id}"
        )));
    };

    let query = pool.prepare_query(
        "INSERT INTO wiki_pages (id, project_id, slug, title, body, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
    );

    sqlx::query(&query)
        .bind(id)
        .bind(project_id)
        .bind(slug)
        .bind(title)
        .bind(body)
        .bind(created_by)
        .bind(created_by)
        .execute(&mut *tx)
        .await
        .map_err(|e| {
            let msg = e.to_string();
            if msg.contains("UNIQUE constraint failed") || msg.contains("duplicate key") {
                SchedulerError::Conflict(format!("wiki page slug already exists: {slug}"))
            } else {
                SchedulerError::Database(format!("create wiki page failed: {e}"))
            }
        })?;

    if tags.is_some() {
        sync_tags_in_tx(&mut tx, pool, id, &held_tags).await?;
    }

    let created = page_with_tags_in_tx(&mut tx, pool, project_id, slug)
        .await?
        .ok_or_else(|| SchedulerError::Database("created page vanished".into()))?;

    if let Some(session_id) = created_by
        && !crate::db::sessions::is_live_in_tx(&mut tx, pool, session_id).await?
    {
        rollback(tx).await?;
        return Err(SchedulerError::Unauthorized("session is not live".into()));
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit wiki create failed: {e}")))?;

    Ok(CreateOutcome::Created(
        Box::new(created.0),
        created.1,
        locked_project_slug,
    ))
}

pub async fn get_page_by_slug(
    pool: &DbPool,
    project_id: &str,
    slug: &str,
) -> Result<WikiPage, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, project_id, slug, title, body, revision_number, created_by, updated_by, \
         {created_fmt} as created_at, {updated_fmt} as updated_at \
         FROM wiki_pages WHERE project_id = ? AND slug = ? AND deleted_at IS NULL"
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(project_id)
        .bind(slug)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("wiki page not found: {slug}"))
            }
            _ => SchedulerError::Database(format!("get wiki page failed: {e}")),
        })?;

    parse_wiki_page_row(row)
}

pub async fn list_pages(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<WikiPageListItem>, SchedulerError> {
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT slug, title, revision_number, updated_by, \
         {updated_fmt} as updated_at \
         FROM wiki_pages WHERE project_id = ? AND deleted_at IS NULL \
         ORDER BY updated_at DESC"
    );

    let prepared = pool.prepare_query(&sql);

    let rows = sqlx::query(&prepared)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki pages failed: {e}")))?;

    rows.into_iter().map(parse_wiki_page_list_row).collect()
}

/// Return every non-deleted wiki page with full content (for search indexing).
pub async fn list_all_pages_for_indexing(pool: &DbPool) -> Result<Vec<WikiPage>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, project_id, slug, title, body, revision_number, created_by, updated_by, \
         {created_fmt} as created_at, {updated_fmt} as updated_at \
         FROM wiki_pages WHERE deleted_at IS NULL"
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("list wiki pages for indexing failed: {e}"))
        })?;

    rows.into_iter().map(parse_wiki_page_row).collect()
}

/// Tag names for every non-deleted page, keyed by page id and sorted by name.
pub async fn tags_by_page(
    pool: &DbPool,
) -> Result<std::collections::HashMap<String, Vec<String>>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT pt.page_id, t.name \
         FROM wiki_page_tags pt \
         JOIN wiki_tags t ON t.id = pt.tag_id \
         JOIN wiki_pages p ON p.id = pt.page_id \
         WHERE p.deleted_at IS NULL \
         ORDER BY t.name",
    );

    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("load wiki page tags failed: {e}")))?;

    let mut map: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
    for row in rows {
        let page_id: String = row.get("page_id");
        let name: String = row.get("name");
        map.entry(page_id).or_default().push(name);
    }
    Ok(map)
}

/// Soft-delete one page, addressed by the id the caller was authorized against.
pub async fn delete_page(
    pool: &DbPool,
    project_id: &str,
    page_id: &str,
    session_id: Option<&str>,
) -> Result<(), SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    if crate::db::projects::lock_active_in_tx(&mut tx, pool, project_id)
        .await?
        .is_none()
    {
        rollback(tx).await?;
        return Err(SchedulerError::NotFound(PAGE_NOT_FOUND_MESSAGE.to_string()));
    }

    let query = pool.prepare_query(
        "UPDATE wiki_pages SET deleted_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
    );

    let result = sqlx::query(&query)
        .bind(page_id)
        .bind(project_id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("delete wiki page failed: {e}")))?;

    if result.rows_affected() == 0 {
        rollback(tx).await?;
        return Err(SchedulerError::NotFound(PAGE_NOT_FOUND_MESSAGE.to_string()));
    }

    if let Some(session_id) = session_id
        && !crate::db::sessions::is_live_in_tx(&mut tx, pool, session_id).await?
    {
        rollback(tx).await?;
        return Err(SchedulerError::Unauthorized("session is not live".into()));
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit wiki delete failed: {e}")))?;

    Ok(())
}

pub async fn list_revisions(
    pool: &DbPool,
    page_id: &str,
) -> Result<Vec<WikiRevisionSummary>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT revision_number, title, summary, created_by, \
         {created_fmt} as created_at \
         FROM wiki_page_revisions WHERE page_id = ? ORDER BY revision_number DESC"
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(page_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki revisions failed: {e}")))?;

    rows.into_iter()
        .map(parse_wiki_revision_summary_row)
        .collect()
}

pub async fn get_revision(
    pool: &DbPool,
    page_id: &str,
    revision_number: i64,
) -> Result<WikiPageRevision, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);

    let sql = format!(
        "SELECT id, page_id, revision_number, title, body, summary, created_by, \
         {created_fmt} as created_at \
         FROM wiki_page_revisions WHERE page_id = ? AND revision_number = ?"
    );
    let query = pool.prepare_query(&sql);

    let row = sqlx::query(&query)
        .bind(page_id)
        .bind(revision_number)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| match e {
            sqlx::Error::RowNotFound => {
                SchedulerError::NotFound(format!("wiki revision not found: {revision_number}"))
            }
            _ => SchedulerError::Database(format!("get wiki revision failed: {e}")),
        })?;

    parse_wiki_revision_row(row)
}

/// Check if a non-deleted page exists for the given project and slug, returning its id.
pub async fn page_id_by_slug(
    pool: &DbPool,
    project_id: &str,
    slug: &str,
) -> Result<Option<String>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT id FROM wiki_pages WHERE project_id = ? AND slug = ? AND deleted_at IS NULL",
    );

    let row = sqlx::query(&query)
        .bind(project_id)
        .bind(slug)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("check wiki page exists failed: {e}")))?;

    Ok(row.map(|r| r.get("id")))
}

fn parse_wiki_page_list_row(row: sqlx::any::AnyRow) -> Result<WikiPageListItem, SchedulerError> {
    Ok(WikiPageListItem {
        slug: row.get("slug"),
        title: row.get("title"),
        revision_number: row.get("revision_number"),
        updated_by: row.get("updated_by"),
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}

fn parse_wiki_page_row(row: sqlx::any::AnyRow) -> Result<WikiPage, SchedulerError> {
    Ok(WikiPage {
        id: row.get("id"),
        project_id: row.get("project_id"),
        slug: row.get("slug"),
        title: row.get("title"),
        body: row.get("body"),
        revision_number: row.get("revision_number"),
        created_by: row.get("created_by"),
        updated_by: row.get("updated_by"),
        created_at: parse_timestamp(&row, "created_at")?,
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}

fn parse_wiki_revision_summary_row(
    row: sqlx::any::AnyRow,
) -> Result<WikiRevisionSummary, SchedulerError> {
    Ok(WikiRevisionSummary {
        revision_number: row.get("revision_number"),
        title: row.get("title"),
        summary: row.get("summary"),
        created_by: row.get("created_by"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}

fn parse_wiki_revision_row(row: sqlx::any::AnyRow) -> Result<WikiPageRevision, SchedulerError> {
    Ok(WikiPageRevision {
        id: row.get("id"),
        page_id: row.get("page_id"),
        revision_number: row.get("revision_number"),
        title: row.get("title"),
        body: row.get("body"),
        summary: row.get("summary"),
        created_by: row.get("created_by"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}

/// Sync page tags within an existing transaction.
/// Replace a page's tag associations from tags already held by phase one.
async fn sync_tags_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    page_id: &str,
    held: &[(String, String)],
) -> Result<(), SchedulerError> {
    let delete_sql = pool.prepare_query("DELETE FROM wiki_page_tags WHERE page_id = ?");
    sqlx::query(&delete_sql)
        .bind(page_id)
        .execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("delete wiki page tags failed: {e}")))?;

    for (_, resolved_id) in held {
        let insert_assoc = if pool.is_postgres() {
            pool.prepare_query(
                "INSERT INTO wiki_page_tags (page_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            )
        } else {
            "INSERT OR IGNORE INTO wiki_page_tags (page_id, tag_id) VALUES (?, ?)".to_string()
        };
        sqlx::query(&insert_assoc)
            .bind(page_id)
            .bind(resolved_id)
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("add wiki page tag failed: {e}")))?;
    }

    Ok(())
}

pub struct TagWithCount {
    pub name: String,
    pub page_count: i64,
}

/// Get or create a tag by name (race-safe via INSERT ON CONFLICT).
pub async fn get_or_create_tag(pool: &DbPool, name: &str) -> Result<String, SchedulerError> {
    let id = Uuid::new_v4().to_string();

    let insert_sql = if pool.is_postgres() {
        "INSERT INTO wiki_tags (id, name) VALUES (?, ?) ON CONFLICT (name) DO NOTHING"
    } else {
        "INSERT OR IGNORE INTO wiki_tags (id, name) VALUES (?, ?)"
    };
    let insert_query = pool.prepare_query(insert_sql);
    sqlx::query(&insert_query)
        .bind(&id)
        .bind(name)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("insert wiki tag failed: {e}")))?;

    let select_query = pool.prepare_query("SELECT id FROM wiki_tags WHERE name = ?");
    let row = sqlx::query(&select_query)
        .bind(name)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("select wiki tag failed: {e}")))?;

    Ok(row.get("id"))
}

/// Add a tag association to a page (idempotent).
pub async fn add_page_tag(
    pool: &DbPool,
    page_id: &str,
    tag_id: &str,
) -> Result<(), SchedulerError> {
    let sql = if pool.is_postgres() {
        "INSERT INTO wiki_page_tags (page_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING"
    } else {
        "INSERT OR IGNORE INTO wiki_page_tags (page_id, tag_id) VALUES (?, ?)"
    };
    let query = pool.prepare_query(sql);
    sqlx::query(&query)
        .bind(page_id)
        .bind(tag_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("add wiki page tag failed: {e}")))?;
    Ok(())
}

/// Remove all tag associations for a page.
pub async fn delete_page_tags(pool: &DbPool, page_id: &str) -> Result<(), SchedulerError> {
    let query = pool.prepare_query("DELETE FROM wiki_page_tags WHERE page_id = ?");
    sqlx::query(&query)
        .bind(page_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete wiki page tags failed: {e}")))?;
    Ok(())
}

/// List tag names for a page, sorted alphabetically.
pub async fn list_page_tags(pool: &DbPool, page_id: &str) -> Result<Vec<String>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT t.name FROM wiki_tags t \
         JOIN wiki_page_tags pt ON pt.tag_id = t.id \
         WHERE pt.page_id = ? ORDER BY t.name",
    );
    let rows = sqlx::query(&query)
        .bind(page_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki page tags failed: {e}")))?;
    Ok(rows.iter().map(|r| r.get("name")).collect())
}

/// List tags with page counts for a project (excludes deleted pages).
pub async fn list_tags_with_counts(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<TagWithCount>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT t.name, COUNT(pt.page_id) as page_count \
         FROM wiki_tags t \
         JOIN wiki_page_tags pt ON pt.tag_id = t.id \
         JOIN wiki_pages p ON p.id = pt.page_id \
         WHERE p.project_id = ? AND p.deleted_at IS NULL \
         GROUP BY t.id, t.name \
         ORDER BY t.name",
    );
    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki tags failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| TagWithCount {
            name: r.get("name"),
            page_count: r.get("page_count"),
        })
        .collect())
}

/// Batch-fetch all (slug, tag_name) pairs for non-deleted pages in a project.
pub async fn list_page_tags_for_project(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<(String, String)>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT p.slug, t.name as tag_name \
         FROM wiki_page_tags pt \
         JOIN wiki_pages p ON p.id = pt.page_id \
         JOIN wiki_tags t ON t.id = pt.tag_id \
         WHERE p.project_id = ? AND p.deleted_at IS NULL \
         ORDER BY p.slug, t.name",
    );
    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list page tags for project failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| (r.get("slug"), r.get("tag_name")))
        .collect())
}

pub struct WikiSubscription {
    pub id: String,
    pub project_id: String,
    pub subscriber: String,
    pub page_slug: Option<String>,
    pub tag_name: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// Create a subscription (idempotent via partial unique indexes).
/// Returns (subscription, was_created).
pub async fn create_subscription(
    pool: &DbPool,
    id: &str,
    project_id: &str,
    subscriber: &str,
    page_slug: Option<&str>,
    tag_name: Option<&str>,
    session_id: Option<&str>,
) -> Result<(WikiSubscription, bool), SchedulerError> {
    let insert_sql = if pool.is_postgres() {
        "INSERT INTO wiki_subscriptions (id, project_id, subscriber, page_slug, tag_name) \
         VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"
    } else {
        "INSERT OR IGNORE INTO wiki_subscriptions (id, project_id, subscriber, page_slug, tag_name) \
         VALUES (?, ?, ?, ?, ?)"
    };
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    if crate::db::projects::lock_active_in_tx(&mut tx, pool, project_id)
        .await?
        .is_none()
    {
        rollback(tx).await?;
        return Err(SchedulerError::NotFound(format!(
            "project not found: {project_id}"
        )));
    }

    let insert_query = pool.prepare_query(insert_sql);
    let result = sqlx::query(&insert_query)
        .bind(id)
        .bind(project_id)
        .bind(subscriber)
        .bind(page_slug)
        .bind(tag_name)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("create wiki subscription failed: {e}")))?;

    let was_created = result.rows_affected() > 0;

    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let (fetch_sql, target_value): (String, &str) = if let Some(slug) = page_slug {
        (
            format!(
                "SELECT id, project_id, subscriber, page_slug, tag_name, \
                 {created_fmt} as created_at \
                 FROM wiki_subscriptions \
                 WHERE project_id = ? AND subscriber = ? AND page_slug = ?"
            ),
            slug,
        )
    } else if let Some(tag) = tag_name {
        (
            format!(
                "SELECT id, project_id, subscriber, page_slug, tag_name, \
                 {created_fmt} as created_at \
                 FROM wiki_subscriptions \
                 WHERE project_id = ? AND subscriber = ? AND tag_name = ?"
            ),
            tag,
        )
    } else {
        rollback(tx).await?;
        return Err(SchedulerError::ValidationFailed(
            "exactly one of page_slug or tag_name must be provided".into(),
        ));
    };
    let fetch_query = pool.prepare_query(&fetch_sql);
    let row = sqlx::query(&fetch_query)
        .bind(project_id)
        .bind(subscriber)
        .bind(target_value)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("fetch wiki subscription failed: {e}")))?;

    let subscription = parse_subscription_row(row)?;
    if let Some(session_id) = session_id
        && !crate::db::sessions::is_live_in_tx(&mut tx, pool, session_id).await?
    {
        rollback(tx).await?;
        return Err(SchedulerError::Unauthorized("session is not live".into()));
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit subscription failed: {e}")))?;

    Ok((subscription, was_created))
}

/// Delete a subscription by id, scoped to project.
pub async fn delete_subscription(
    pool: &DbPool,
    project_id: &str,
    id: &str,
    session_id: Option<&str>,
) -> Result<(), SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let query =
        pool.prepare_query("DELETE FROM wiki_subscriptions WHERE id = ? AND project_id = ?");
    let result = sqlx::query(&query)
        .bind(id)
        .bind(project_id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("delete wiki subscription failed: {e}")))?;
    if result.rows_affected() == 0 {
        rollback(tx).await?;
        return Err(SchedulerError::NotFound(format!(
            "subscription not found: {id}"
        )));
    }

    if let Some(session_id) = session_id
        && !crate::db::sessions::is_live_in_tx(&mut tx, pool, session_id).await?
    {
        rollback(tx).await?;
        return Err(SchedulerError::Unauthorized("session is not live".into()));
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit subscription delete failed: {e}")))?;
    Ok(())
}

/// List subscriptions for a subscriber in a project.
pub async fn list_subscriptions(
    pool: &DbPool,
    project_id: &str,
    subscriber: &str,
) -> Result<Vec<WikiSubscription>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = format!(
        "SELECT id, project_id, subscriber, page_slug, tag_name, \
         {created_fmt} as created_at \
         FROM wiki_subscriptions \
         WHERE project_id = ? AND subscriber = ? \
         ORDER BY created_at"
    );
    let query = pool.prepare_query(&sql);
    let rows = sqlx::query(&query)
        .bind(project_id)
        .bind(subscriber)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki subscriptions failed: {e}")))?;
    rows.into_iter().map(parse_subscription_row).collect()
}

/// `list_subscriptions`, read on a held transaction.
pub async fn list_subscriptions_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
    subscriber: &str,
) -> Result<Vec<WikiSubscription>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = format!(
        "SELECT id, project_id, subscriber, page_slug, tag_name, \
         {created_fmt} as created_at \
         FROM wiki_subscriptions \
         WHERE project_id = ? AND subscriber = ? \
         ORDER BY created_at"
    );
    let query = pool.prepare_query(&sql);
    let rows = sqlx::query(&query)
        .bind(project_id)
        .bind(subscriber)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki subscriptions failed: {e}")))?;
    rows.into_iter().map(parse_subscription_row).collect()
}

fn parse_subscription_row(row: sqlx::any::AnyRow) -> Result<WikiSubscription, SchedulerError> {
    Ok(WikiSubscription {
        id: row.get("id"),
        project_id: row.get("project_id"),
        subscriber: row.get("subscriber"),
        page_slug: row.get("page_slug"),
        tag_name: row.get("tag_name"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}

pub struct WikiChange {
    pub slug: String,
    pub title: String,
    pub revision_number: i64,
    pub summary: Option<String>,
    pub created_by: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// List recent wiki changes (revisions) for a project.
pub async fn list_changes(
    pool: &DbPool,
    project_id: &str,
    since: Option<&str>,
    execution_id: Option<&str>,
    limit: i64,
) -> Result<Vec<WikiChange>, SchedulerError> {
    let created_fmt = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", "r.created_at");

    let mut sql = format!(
        "SELECT p.slug, r.title, r.revision_number, r.summary, r.created_by, \
         {created_fmt} as created_at \
         FROM wiki_page_revisions r \
         JOIN wiki_pages p ON p.id = r.page_id \
         WHERE p.project_id = ? AND p.deleted_at IS NULL"
    );

    let mut binds: Vec<String> = vec![project_id.to_string()];

    if let Some(since_ts) = since {
        if pool.is_postgres() {
            sql.push_str(" AND r.created_at >= ?::timestamptz");
        } else {
            sql.push_str(" AND datetime(r.created_at) >= datetime(?)");
        }
        binds.push(since_ts.to_string());
    }

    if let Some(exec_id) = execution_id {
        sql.push_str(" AND r.created_by IN (SELECT id FROM sessions WHERE execution_id = ?)");
        binds.push(exec_id.to_string());
    }

    sql.push_str(&format!(
        " ORDER BY r.created_at DESC, r.revision_number DESC LIMIT {limit}"
    ));

    let query = pool.prepare_query(&sql);
    let mut q = sqlx::query(&query);
    for b in &binds {
        q = q.bind(b);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki changes failed: {e}")))?;

    rows.into_iter().map(parse_change_row).collect()
}

fn parse_change_row(row: sqlx::any::AnyRow) -> Result<WikiChange, SchedulerError> {
    Ok(WikiChange {
        slug: row.get("slug"),
        title: row.get("title"),
        revision_number: row.get("revision_number"),
        summary: row.get("summary"),
        created_by: row.get("created_by"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}

/// A caller writing to a page its own project does not own.
pub struct ForeignWriter<'a> {
    pub caller_project_id: &'a str,
    pub owner_project_id: &'a str,
}

/// One `PATCH` worth of change, applied under a single transaction.
pub struct PageChange<'a> {
    pub project_id: &'a str,
    pub slug: &'a str,
    /// The page the caller was authorized against.
    pub expected_page_id: &'a str,
    pub expected_revision: i64,
    /// `(old, new)` — the rename only lands when `old` matches the stored title.
    pub title: Option<(&'a str, &'a str)>,
    pub add_tags: &'a [String],
    pub remove_tags: &'a [String],
    pub summary: Option<&'a str>,
    pub updated_by: Option<&'a str>,
    /// Present only when the caller's project does not own the page.
    pub foreign: Option<ForeignWriter<'a>>,
    pub acknowledge_share: bool,
}

/// Why a body transform could not be applied.
pub struct BodyEditFailure {
    /// The edit at fault, or `None` when the combined RESULT is what fails.
    pub index: Option<usize>,
    pub reason: BodyEditReason,
}

pub enum BodyEditReason {
    NotFound,
    MultipleMatches,
    /// A page body is never empty: an empty one can never be edited back into
    /// content, because `old_string` must be non-empty and nothing matches it.
    EmptyResult,
}

impl BodyEditReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            BodyEditReason::NotFound => "not_found",
            BodyEditReason::MultipleMatches => "multiple_matches",
            BodyEditReason::EmptyResult => "empty_body",
        }
    }
}

pub enum PageChangeOutcome {
    /// The change landed and the revision advanced.
    Updated(WikiPage, Vec<String>),
    /// Nothing about the page differed, so no revision was recorded.
    Unchanged(WikiPage, Vec<String>),
    RevisionConflict(WikiPage, Vec<String>),
    TitleMismatch(WikiPage, Vec<String>),
    TagNotPresent {
        missing: Vec<String>,
        current: WikiPage,
        tags: Vec<String>,
    },
    BodyEditFailed {
        failure: BodyEditFailure,
        current: WikiPage,
        tags: Vec<String>,
    },
    /// Every tag this request would publish into, not only the first.
    NeedsConfirmation(Vec<PublicationAudience>),
    /// The page exists but the caller may no longer reach it.
    AccessRevoked,
    /// The caller may reach the page but not write to it.
    ReadOnly,
    /// Cross-project writes were switched off before this one could commit.
    CrossProjectWritesDisabled,
    /// The session that authorized this write ended before it could commit.
    PrincipalGone,
    NotFound,
}

/// Apply one page change, or report why it was refused.
///
/// `transform_body` receives the body read inside the transaction and returns
/// the replacement.
pub async fn apply_page_change<F>(
    pool: &DbPool,
    change: PageChange<'_>,
    transform_body: F,
) -> Result<(PageChangeOutcome, Option<String>), SchedulerError>
where
    F: Fn(&str) -> Result<String, BodyEditFailure>,
{
    let for_update = if pool.is_postgres() {
        " FOR UPDATE"
    } else {
        ""
    };

    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin transaction failed: {e}")))?;

    let held_tags = materialize_tags_in_tx(&mut tx, pool, change.add_tags).await?;

    let audiences = if !change.add_tags.is_empty() && !change.acknowledge_share {
        audiences_for_publication(&mut tx, pool, change.project_id, change.slug, &held_tags).await?
    } else {
        Vec::new()
    };

    let page_id = change.expected_page_id.to_string();

    let mut caller_levels: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    let mut owner_tags: std::collections::HashSet<String> = std::collections::HashSet::new();

    let mut project_ids: Vec<&str> = vec![change.project_id];
    if let Some(ref foreign) = change.foreign
        && foreign.caller_project_id != change.project_id
    {
        project_ids.push(foreign.caller_project_id);
    }
    project_ids.sort_unstable();

    let for_share = if pool.is_postgres() { " FOR SHARE" } else { "" };
    let mut locked_project_slug: Option<String> = None;
    for project_id in &project_ids {
        let project_sql =
            format!("SELECT id, slug FROM projects WHERE id = ? AND deleted_at IS NULL{for_share}");
        let found = sqlx::query(&pool.prepare_query(&project_sql))
            .bind(project_id)
            .fetch_optional(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("lock project failed: {e}")))?;

        let Some(row) = found else {
            rollback(tx).await?;
            return Ok((PageChangeOutcome::AccessRevoked, None));
        };
        if *project_id == change.project_id {
            locked_project_slug = Some(row.get::<String, _>("slug"));
        }
    }

    if let Some(ref foreign) = change.foreign {
        let sql = format!(
            "SELECT tag_id, project_id, access_level FROM wiki_tag_members \
             WHERE project_id IN (?, ?) AND revoked_at IS NULL \
             ORDER BY project_id, tag_id{for_update}"
        );
        let rows = sqlx::query(&pool.prepare_query(&sql))
            .bind(project_ids[0])
            .bind(project_ids[1])
            .fetch_all(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("lock tag membership failed: {e}")))?;

        for row in &rows {
            let tag_id: String = row.get("tag_id");
            let project_id: String = row.get("project_id");
            if project_id == foreign.caller_project_id {
                caller_levels.insert(tag_id, row.get("access_level"));
            } else {
                owner_tags.insert(tag_id);
            }
        }
    }

    let tag_sql = format!("SELECT tag_id FROM wiki_page_tags WHERE page_id = ?{for_update}");
    let tag_rows = sqlx::query(&pool.prepare_query(&tag_sql))
        .bind(&page_id)
        .fetch_all(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("lock page tag associations failed: {e}")))?;

    let mut foreign_access: Option<String> = None;
    if change.foreign.is_some() {
        let mut best: Option<&str> = None;
        for row in &tag_rows {
            let tag_id: String = row.get("tag_id");
            if !owner_tags.contains(&tag_id) {
                continue;
            }
            if let Some(level) = caller_levels.get(&tag_id) {
                if level == "read_write" {
                    best = Some("read_write");
                } else if best.is_none() {
                    best = Some("read");
                }
            }
        }

        match best {
            None => {
                rollback(tx).await?;
                return Ok((PageChangeOutcome::AccessRevoked, locked_project_slug));
            }
            Some(level) => foreign_access = Some(level.to_string()),
        }
    }

    if foreign_access.as_deref() == Some("read") {
        rollback(tx).await?;
        return Ok((PageChangeOutcome::ReadOnly, locked_project_slug));
    }

    let page_sql = format!(
        "SELECT id, revision_number, title, body FROM wiki_pages \
         WHERE id = ? AND project_id = ? AND slug = ? AND deleted_at IS NULL{for_update}"
    );
    let page_row = sqlx::query(&pool.prepare_query(&page_sql))
        .bind(&page_id)
        .bind(change.project_id)
        .bind(change.slug)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("lock wiki page failed: {e}")))?;

    let Some(page_row) = page_row else {
        rollback(tx).await?;
        return Ok((PageChangeOutcome::NotFound, locked_project_slug));
    };

    let page_id: String = page_row.get("id");
    let current_revision: i64 = page_row.get("revision_number");
    let current_title: String = page_row.get("title");
    let current_body: String = page_row.get("body");

    if change.foreign.is_some() && !cross_project_writes_in_tx(&mut tx, pool).await {
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::CrossProjectWritesDisabled,
            locked_project_slug,
        ));
    }

    if current_revision != change.expected_revision {
        let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
            .await?
            .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::RevisionConflict(current.0, current.1),
            locked_project_slug,
        ));
    }

    if let Some((old_title, _)) = change.title
        && old_title != current_title
    {
        let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
            .await?
            .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::TitleMismatch(current.0, current.1),
            locked_project_slug,
        ));
    }

    let current_tags = page_tag_names_in_tx(&mut tx, pool, &page_id).await?;

    let missing: Vec<String> = change
        .remove_tags
        .iter()
        .filter(|t| !current_tags.iter().any(|c| c == *t))
        .cloned()
        .collect();
    if !missing.is_empty() {
        let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
            .await?
            .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::TagNotPresent {
                missing,
                current: current.0,
                tags: current.1,
            },
            locked_project_slug,
        ));
    }

    let new_body = match transform_body(&current_body) {
        Ok(body) => body,
        Err(failure) => {
            let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
                .await?
                .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
            rollback(tx).await?;
            return Ok((
                PageChangeOutcome::BodyEditFailed {
                    failure,
                    current: current.0,
                    tags: current.1,
                },
                locked_project_slug,
            ));
        }
    };

    let new_title = change.title.map(|(_, new)| new).unwrap_or(&current_title);

    let added: Vec<String> = ordered_tag_names(change.add_tags)
        .into_iter()
        .filter(|t| !current_tags.iter().any(|c| c == t))
        .collect();
    let owed: Vec<PublicationAudience> = audiences
        .into_iter()
        .filter(|a| added.contains(&a.tag_name))
        .collect();
    if !owed.is_empty() {
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::NeedsConfirmation(owed),
            locked_project_slug,
        ));
    }

    let tags_changed = !added.is_empty() || !change.remove_tags.is_empty();
    let content_changed = new_body != current_body || new_title != current_title;

    if !tags_changed && !content_changed {
        let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
            .await?
            .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
        tx.rollback()
            .await
            .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))?;
        return Ok((
            PageChangeOutcome::Unchanged(current.0, current.1),
            locked_project_slug,
        ));
    }

    let revision_id = Uuid::new_v4().to_string();
    let archive_sql = pool.prepare_query(
        "INSERT INTO wiki_page_revisions (id, page_id, revision_number, title, body, summary, created_by, created_at) \
         SELECT ?, id, revision_number, title, body, ?, ?, CURRENT_TIMESTAMP \
         FROM wiki_pages \
         WHERE id = ? AND revision_number = ? AND deleted_at IS NULL",
    );
    let archived = sqlx::query(&archive_sql)
        .bind(&revision_id)
        .bind(change.summary)
        .bind(change.updated_by)
        .bind(&page_id)
        .bind(change.expected_revision)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("archive wiki page revision failed: {e}")))?;

    if archived.rows_affected() == 0 {
        let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
            .await?
            .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::RevisionConflict(current.0, current.1),
            locked_project_slug,
        ));
    }

    let update_sql = pool.prepare_query(
        "UPDATE wiki_pages SET title = ?, body = ?, revision_number = revision_number + 1, \
         updated_by = ?, updated_at = CURRENT_TIMESTAMP \
         WHERE id = ? AND revision_number = ? AND deleted_at IS NULL",
    );
    let updated = sqlx::query(&update_sql)
        .bind(new_title)
        .bind(&new_body)
        .bind(change.updated_by)
        .bind(&page_id)
        .bind(change.expected_revision)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("update wiki page failed: {e}")))?;

    if updated.rows_affected() == 0 {
        let current = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
            .await?
            .ok_or_else(|| SchedulerError::Database("page vanished".into()))?;
        rollback(tx).await?;
        return Ok((
            PageChangeOutcome::RevisionConflict(current.0, current.1),
            locked_project_slug,
        ));
    }

    for tag_name in &added {
        let tag_id = held_tags
            .iter()
            .find(|(name, _)| name == tag_name)
            .map(|(_, id)| id.clone())
            .ok_or_else(|| SchedulerError::Database(format!("tag not held: {tag_name}")))?;
        let insert_assoc = if pool.is_postgres() {
            pool.prepare_query(
                "INSERT INTO wiki_page_tags (page_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            )
        } else {
            "INSERT OR IGNORE INTO wiki_page_tags (page_id, tag_id) VALUES (?, ?)".to_string()
        };
        sqlx::query(&insert_assoc)
            .bind(&page_id)
            .bind(&tag_id)
            .execute(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("add wiki page tag failed: {e}")))?;
    }

    for tag_name in change.remove_tags {
        let delete_assoc = pool.prepare_query(
            "DELETE FROM wiki_page_tags WHERE page_id = ? \
             AND tag_id IN (SELECT id FROM wiki_tags WHERE name = ?)",
        );
        sqlx::query(&delete_assoc)
            .bind(&page_id)
            .bind(tag_name)
            .execute(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("remove wiki page tag failed: {e}")))?;
    }

    let updated = page_with_tags_by_id_in_tx(&mut tx, pool, &page_id)
        .await?
        .ok_or_else(|| SchedulerError::Database("updated page vanished".into()))?;

    if let Some(session_id) = change.updated_by
        && !crate::db::sessions::is_live_in_tx(&mut tx, pool, session_id).await?
    {
        rollback(tx).await?;
        return Ok((PageChangeOutcome::PrincipalGone, locked_project_slug));
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit wiki update failed: {e}")))?;

    Ok((
        PageChangeOutcome::Updated(updated.0, updated.1),
        locked_project_slug,
    ))
}

/// The kill switch as it stands inside this transaction.
pub(crate) async fn cross_project_writes_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
) -> bool {
    match crate::db::config::value_in_tx(tx, pool, CROSS_PROJECT_WRITES_FLAG).await {
        Ok(None) => true,
        Ok(Some(value)) => match value.as_str() {
            "false" => false,
            "true" => true,
            other => {
                tracing::warn!(
                    "config '{}' has invalid value '{}' in transaction, using default (enabled)",
                    CROSS_PROJECT_WRITES_FLAG,
                    other
                );
                true
            }
        },
        Err(e) => {
            tracing::warn!(
                error = %e,
                "cross-project writes: in-transaction config read failed; refusing the write"
            );
            false
        }
    }
}

async fn rollback(tx: sqlx::Transaction<'_, sqlx::Any>) -> Result<(), SchedulerError> {
    tx.rollback()
        .await
        .map_err(|e| SchedulerError::Database(format!("rollback failed: {e}")))
}

async fn page_id_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
    slug: &str,
) -> Result<Option<String>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT id FROM wiki_pages WHERE project_id = ? AND slug = ? AND deleted_at IS NULL",
    );
    let row = sqlx::query(&sql)
        .bind(project_id)
        .bind(slug)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("fetch page id failed: {e}")))?;
    Ok(row.map(|r| r.get("id")))
}

async fn page_tag_names_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    page_id: &str,
) -> Result<Vec<String>, SchedulerError> {
    let sql = pool.prepare_query(
        "SELECT t.name FROM wiki_tags t \
         JOIN wiki_page_tags pt ON pt.tag_id = t.id \
         WHERE pt.page_id = ? ORDER BY t.name",
    );
    let rows = sqlx::query(&sql)
        .bind(page_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list page tags failed: {e}")))?;
    Ok(rows.iter().map(|r| r.get("name")).collect())
}

pub struct PageSummary {
    pub page_id: String,
    pub project_id: String,
    pub slug: String,
    pub title: String,
    pub revision_number: i64,
    pub updated_by: Option<String>,
    pub updated_at: DateTime<Utc>,
}

pub struct ChangeSummary {
    pub page_id: String,
    pub project_id: String,
    pub slug: String,
    pub title: String,
    pub revision_number: i64,
    pub summary: Option<String>,
    pub created_by: Option<String>,
    pub created_at: DateTime<Utc>,
}

fn placeholders(count: usize) -> String {
    std::iter::repeat_n("?", count)
        .collect::<Vec<_>>()
        .join(", ")
}

/// Non-deleted pages across several projects, newest first.
pub async fn list_pages_in_projects_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_ids: &[String],
) -> Result<Vec<PageSummary>, SchedulerError> {
    if project_ids.is_empty() {
        return Ok(Vec::new());
    }
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);
    let sql = format!(
        "SELECT id, project_id, slug, title, revision_number, updated_by, \
         {updated_fmt} as updated_at \
         FROM wiki_pages WHERE project_id IN ({}) AND deleted_at IS NULL \
         ORDER BY updated_at DESC",
        placeholders(project_ids.len())
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);
    for pid in project_ids {
        q = q.bind(pid);
    }
    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki pages failed: {e}")))?;
    rows.into_iter().map(parse_page_summary_row).collect()
}

/// `(page_id, tag_name)` pairs for non-deleted pages across several projects.
pub async fn page_tags_in_projects_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_ids: &[String],
) -> Result<std::collections::HashMap<String, Vec<String>>, SchedulerError> {
    if project_ids.is_empty() {
        return Ok(std::collections::HashMap::new());
    }
    let sql = format!(
        "SELECT pt.page_id, t.name \
         FROM wiki_page_tags pt \
         JOIN wiki_tags t ON t.id = pt.tag_id \
         JOIN wiki_pages p ON p.id = pt.page_id \
         WHERE p.project_id IN ({}) AND p.deleted_at IS NULL \
         ORDER BY t.name",
        placeholders(project_ids.len())
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);
    for pid in project_ids {
        q = q.bind(pid);
    }
    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki page tags failed: {e}")))?;
    let mut map: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
    for row in rows {
        let page_id: String = row.get("page_id");
        map.entry(page_id).or_default().push(row.get("name"));
    }
    Ok(map)
}

/// Recent revisions across several projects, newest first.
pub async fn list_changes_in_projects_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_ids: &[String],
    since: Option<&str>,
    execution_id: Option<&str>,
) -> Result<Vec<ChangeSummary>, SchedulerError> {
    if project_ids.is_empty() {
        return Ok(Vec::new());
    }
    let (sql, binds) = changes_query(pool, project_ids, since, execution_id);
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);
    for b in &binds {
        q = q.bind(b);
    }
    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki changes failed: {e}")))?;
    rows.into_iter().map(parse_change_summary_row).collect()
}

fn parse_page_summary_row(row: sqlx::any::AnyRow) -> Result<PageSummary, SchedulerError> {
    Ok(PageSummary {
        page_id: row.get("id"),
        project_id: row.get("project_id"),
        slug: row.get("slug"),
        title: row.get("title"),
        revision_number: row.get("revision_number"),
        updated_by: row.get("updated_by"),
        updated_at: parse_timestamp(&row, "updated_at")?,
    })
}

fn changes_query(
    pool: &DbPool,
    project_ids: &[String],
    since: Option<&str>,
    execution_id: Option<&str>,
) -> (String, Vec<String>) {
    let created_fmt = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", "r.created_at");

    let mut sql = format!(
        "SELECT p.id as page_id, p.project_id, p.slug, r.title, r.revision_number, \
         r.summary, r.created_by, {created_fmt} as created_at \
         FROM wiki_page_revisions r \
         JOIN wiki_pages p ON p.id = r.page_id \
         WHERE p.project_id IN ({}) AND p.deleted_at IS NULL",
        placeholders(project_ids.len())
    );

    let mut binds: Vec<String> = project_ids.to_vec();

    if let Some(since_ts) = since {
        if pool.is_postgres() {
            sql.push_str(" AND r.created_at >= ?::timestamptz");
        } else {
            sql.push_str(" AND datetime(r.created_at) >= datetime(?)");
        }
        binds.push(since_ts.to_string());
    }

    if let Some(exec_id) = execution_id {
        sql.push_str(" AND r.created_by IN (SELECT id FROM sessions WHERE execution_id = ?)");
        binds.push(exec_id.to_string());
    }

    sql.push_str(" ORDER BY r.created_at DESC, r.revision_number DESC");

    (sql, binds)
}

fn parse_change_summary_row(row: sqlx::any::AnyRow) -> Result<ChangeSummary, SchedulerError> {
    Ok(ChangeSummary {
        page_id: row.get("page_id"),
        project_id: row.get("project_id"),
        slug: row.get("slug"),
        title: row.get("title"),
        revision_number: row.get("revision_number"),
        summary: row.get("summary"),
        created_by: row.get("created_by"),
        created_at: parse_timestamp(&row, "created_at")?,
    })
}

/// Revisions of one page, read on a held transaction.
pub async fn list_revisions_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    page_id: &str,
) -> Result<Vec<WikiRevisionSummary>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = format!(
        "SELECT revision_number, title, summary, created_by, {created_fmt} as created_at \
         FROM wiki_page_revisions WHERE page_id = ? ORDER BY revision_number DESC"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(page_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki revisions failed: {e}")))?;
    rows.into_iter()
        .map(parse_wiki_revision_summary_row)
        .collect()
}

/// One revision of one page, read on a held transaction.
pub async fn get_revision_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    page_id: &str,
    revision_number: i64,
) -> Result<Option<WikiPageRevision>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let sql = format!(
        "SELECT id, page_id, revision_number, title, body, summary, created_by, \
         {created_fmt} as created_at \
         FROM wiki_page_revisions WHERE page_id = ? AND revision_number = ?"
    );
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(page_id)
        .bind(revision_number)
        .fetch_optional(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("get wiki revision failed: {e}")))?;
    row.map(parse_wiki_revision_row).transpose()
}

/// `pages_carrying_tag`, read on a held transaction.
pub async fn pages_carrying_tag_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    tag_id: &str,
    project_ids: &[String],
) -> Result<Vec<(String, String, String, String)>, SchedulerError> {
    if project_ids.is_empty() {
        return Ok(Vec::new());
    }
    let sql = format!(
        "SELECT p.id, p.project_id, p.slug, p.title \
         FROM wiki_pages p \
         JOIN wiki_page_tags pt ON pt.page_id = p.id \
         WHERE pt.tag_id = ? AND p.project_id IN ({}) AND p.deleted_at IS NULL \
         ORDER BY p.project_id, p.slug",
        placeholders(project_ids.len())
    );
    let query = pool.prepare_query(&sql);
    let mut q = sqlx::query(&query).bind(tag_id);
    for pid in project_ids {
        q = q.bind(pid);
    }

    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list pages carrying tag failed: {e}")))?;

    Ok(rows
        .iter()
        .map(|r| {
            (
                r.get("id"),
                r.get("project_id"),
                r.get("slug"),
                r.get("title"),
            )
        })
        .collect())
}

/// Pages `project_id` can already reach through a share tag other than
/// `exclude_tag_id`, as page ids.
///
/// With `write_only`, only pages it can already write.
pub async fn reachable_page_ids_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
    exclude_tag_id: &str,
    write_only: bool,
) -> Result<std::collections::HashSet<String>, SchedulerError> {
    let write_clause = if write_only {
        " AND mine.access_level = 'read_write'"
    } else {
        ""
    };
    let sql = format!(
        "SELECT DISTINCT p.id \
         FROM wiki_tag_members mine \
         JOIN wiki_tag_members theirs \
           ON theirs.tag_id = mine.tag_id \
          AND theirs.project_id <> mine.project_id \
          AND theirs.revoked_at IS NULL \
         JOIN projects caller_project \
           ON caller_project.id = mine.project_id AND caller_project.deleted_at IS NULL \
         JOIN projects owner_project \
           ON owner_project.id = theirs.project_id AND owner_project.deleted_at IS NULL \
         JOIN wiki_page_tags pt ON pt.tag_id = mine.tag_id \
         JOIN wiki_pages p \
           ON p.id = pt.page_id AND p.project_id = theirs.project_id \
          AND p.deleted_at IS NULL \
         WHERE mine.project_id = ? AND mine.revoked_at IS NULL \
           AND mine.tag_id <> ?{write_clause}"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(project_id)
        .bind(exclude_tag_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list reachable pages failed: {e}")))?;
    Ok(rows.iter().map(|r| r.get("id")).collect())
}

/// `(project_id, project_slug)` for members already publishing `slug` through that tag.
async fn members_publishing_slug_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    slug: &str,
    tag_id: &str,
    project_ids: &[String],
) -> Result<Vec<(String, String)>, SchedulerError> {
    if project_ids.is_empty() {
        return Ok(Vec::new());
    }
    let sql = format!(
        "SELECT DISTINCT p.project_id, owner.slug as project_slug FROM wiki_pages p \
         JOIN wiki_page_tags pt ON pt.page_id = p.id \
         JOIN projects owner ON owner.id = p.project_id AND owner.deleted_at IS NULL \
         WHERE p.slug = ? AND pt.tag_id = ? AND p.project_id IN ({}) \
           AND p.deleted_at IS NULL",
        placeholders(project_ids.len())
    );
    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared).bind(slug).bind(tag_id);
    for pid in project_ids {
        q = q.bind(pid);
    }
    let rows = q
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("find slug holders failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| (r.get("project_id"), r.get("project_slug")))
        .collect())
}

pub struct WikiPageExport {
    pub slug: String,
    pub title: String,
    pub body: String,
}

/// Export all non-deleted wiki pages for a project.
pub async fn export_pages(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<WikiPageExport>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT slug, title, body FROM wiki_pages \
         WHERE project_id = ? AND deleted_at IS NULL \
         ORDER BY slug",
    );
    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("export wiki pages failed: {e}")))?;

    Ok(rows
        .iter()
        .map(|r| WikiPageExport {
            slug: r.get("slug"),
            title: r.get("title"),
            body: r.get("body"),
        })
        .collect())
}

/// `export_pages`, read on a held transaction.
pub async fn export_pages_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<WikiPageExport>, SchedulerError> {
    let query = pool.prepare_query(
        "SELECT slug, title, body FROM wiki_pages \
         WHERE project_id = ? AND deleted_at IS NULL \
         ORDER BY slug",
    );
    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("export wiki pages failed: {e}")))?;

    Ok(rows
        .iter()
        .map(|r| WikiPageExport {
            slug: r.get("slug"),
            title: r.get("title"),
            body: r.get("body"),
        })
        .collect())
}
