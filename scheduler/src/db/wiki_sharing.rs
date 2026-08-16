use chrono::{DateTime, Utc};
use sqlx::Row;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

pub const ACCESS_READ: &str = "read";
pub const ACCESS_READ_WRITE: &str = "read_write";

/// True when `level` is one of the two access levels the model defines.
pub fn is_access_level(level: &str) -> bool {
    level == ACCESS_READ || level == ACCESS_READ_WRITE
}

/// An active membership row plus the member project's slug.
pub struct Member {
    pub id: String,
    pub project_id: String,
    pub project_slug: String,
    pub access_level: String,
    pub created_at: DateTime<Utc>,
}

/// A tag and its active members, if any.
pub struct TagWithMembers {
    pub tag_id: String,
    pub name: String,
    pub members: Vec<Member>,
}

/// Config key for the cross-project write kill switch.
pub const CROSS_PROJECT_WRITES_FLAG: &str = "wiki.cross_project_writes";

/// One tag through which the caller reaches another project's pages.
pub struct SharedTagLink {
    pub tag_id: String,
    pub tag_name: String,
    pub owner_project_id: String,
    /// The caller's own access level in that tag.
    pub access_level: String,
}

/// Every tag, with active members inline, ordered by tag name.
pub async fn list_tags_with_members(pool: &DbPool) -> Result<Vec<TagWithMembers>, SchedulerError> {
    let tag_rows = sqlx::query(&pool.prepare_query("SELECT id, name FROM wiki_tags ORDER BY name"))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki tags failed: {e}")))?;

    let created_fmt = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", "m.created_at");
    let sql = format!(
        "SELECT m.id, m.tag_id, m.project_id, p.slug, m.access_level, \
         {created_fmt} as created_at \
         FROM wiki_tag_members m \
         JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL \
         WHERE m.revoked_at IS NULL \
         ORDER BY p.slug"
    );
    let member_rows = sqlx::query(&pool.prepare_query(&sql))
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list tag members failed: {e}")))?;

    let mut by_tag: std::collections::HashMap<String, Vec<Member>> =
        std::collections::HashMap::new();
    for row in member_rows {
        let tag_id: String = row.get("tag_id");
        let project_id: String = row.get("project_id");
        let slug: Option<String> = row.get("slug");
        by_tag.entry(tag_id).or_default().push(Member {
            id: row.get("id"),
            project_slug: slug.unwrap_or_else(|| project_id.clone()),
            project_id,
            access_level: row.get("access_level"),
            created_at: parse_timestamp(&row, "created_at")?,
        });
    }

    Ok(tag_rows
        .into_iter()
        .map(|row| {
            let tag_id: String = row.get("id");
            TagWithMembers {
                name: row.get("name"),
                members: by_tag.remove(&tag_id).unwrap_or_default(),
                tag_id,
            }
        })
        .collect())
}

/// Tag name for an id, or `None` when no such tag exists.
pub async fn tag_name(pool: &DbPool, tag_id: &str) -> Result<Option<String>, SchedulerError> {
    let row = sqlx::query(&pool.prepare_query("SELECT name FROM wiki_tags WHERE id = ?"))
        .bind(tag_id)
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("fetch wiki tag failed: {e}")))?;
    Ok(row.map(|r| r.get("name")))
}

/// Active members of one tag whose projects are also active, ordered by slug.
pub async fn active_members(pool: &DbPool, tag_id: &str) -> Result<Vec<Member>, SchedulerError> {
    let created_fmt = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", "m.created_at");
    let sql = format!(
        "SELECT m.id, m.project_id, p.slug, m.access_level, {created_fmt} as created_at \
         FROM wiki_tag_members m \
         JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL \
         WHERE m.tag_id = ? AND m.revoked_at IS NULL \
         ORDER BY p.slug"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(tag_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list active members failed: {e}")))?;

    rows.into_iter()
        .map(|row| {
            let project_id: String = row.get("project_id");
            let slug: Option<String> = row.get("slug");
            Ok(Member {
                id: row.get("id"),
                project_slug: slug.unwrap_or_else(|| project_id.clone()),
                project_id,
                access_level: row.get("access_level"),
                created_at: parse_timestamp(&row, "created_at")?,
            })
        })
        .collect()
}

/// Active members of one tag, read on a held transaction.
pub async fn active_members_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    tag_id: &str,
) -> Result<Vec<Member>, SchedulerError> {
    let created_fmt = pool
        .format_timestamp(TimestampColumn::CreatedAt)
        .replace("created_at", "m.created_at");
    let sql = format!(
        "SELECT m.id, m.project_id, p.slug, m.access_level, {created_fmt} as created_at \
         FROM wiki_tag_members m \
         JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL \
         WHERE m.tag_id = ? AND m.revoked_at IS NULL \
         ORDER BY p.slug"
    );
    let rows = sqlx::query(&pool.prepare_query(&sql))
        .bind(tag_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list active members failed: {e}")))?;

    rows.into_iter()
        .map(|row| {
            let project_id: String = row.get("project_id");
            let slug: Option<String> = row.get("slug");
            Ok(Member {
                id: row.get("id"),
                project_slug: slug.unwrap_or_else(|| project_id.clone()),
                project_id,
                access_level: row.get("access_level"),
                created_at: parse_timestamp(&row, "created_at")?,
            })
        })
        .collect()
}

/// The active membership of one project in one tag.
pub async fn active_member(
    pool: &DbPool,
    tag_id: &str,
    project_id: &str,
) -> Result<Option<Member>, SchedulerError> {
    Ok(active_members(pool, tag_id)
        .await?
        .into_iter()
        .find(|m| m.project_id == project_id))
}

/// The active membership of one project in one tag, read on a held transaction.
pub async fn active_member_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    tag_id: &str,
    project_id: &str,
) -> Result<Option<Member>, SchedulerError> {
    Ok(active_members_in_tx(tx, pool, tag_id)
        .await?
        .into_iter()
        .find(|m| m.project_id == project_id))
}

/// Begin a transaction holding the tag row.
pub async fn begin_locked_on_tag<'a>(
    pool: &'a DbPool,
    tag_id: &str,
) -> Result<Option<sqlx::Transaction<'a, sqlx::Any>>, SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin tag transaction failed: {e}")))?;

    let for_update = if pool.is_postgres() {
        " FOR UPDATE"
    } else {
        ""
    };
    let sql = format!("SELECT id FROM wiki_tags WHERE id = ?{for_update}");
    let row = sqlx::query(&pool.prepare_query(&sql))
        .bind(tag_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("lock wiki tag failed: {e}")))?;

    match row {
        Some(_) => Ok(Some(tx)),
        None => Ok(None),
    }
}

pub async fn insert_member_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    id: &str,
    tag_id: &str,
    project_id: &str,
    access_level: &str,
    created_by: Option<&str>,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "INSERT INTO wiki_tag_members (id, tag_id, project_id, access_level, created_by) \
         VALUES (?, ?, ?, ?, ?)",
    );
    sqlx::query(&query)
        .bind(id)
        .bind(tag_id)
        .bind(project_id)
        .bind(access_level)
        .bind(created_by)
        .execute(&mut **tx)
        .await
        .map_err(|e| {
            let msg = e.to_string();
            if msg.contains("UNIQUE constraint failed") || msg.contains("duplicate key") {
                SchedulerError::Conflict("member_exists".into())
            } else {
                SchedulerError::Database(format!("insert tag member failed: {e}"))
            }
        })?;
    Ok(())
}

/// Set a member's access level on a held transaction.
pub async fn set_access_level_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    tag_id: &str,
    project_id: &str,
    access_level: &str,
) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE wiki_tag_members SET access_level = ? \
         WHERE tag_id = ? AND project_id = ? AND revoked_at IS NULL",
    );
    let result = sqlx::query(&query)
        .bind(access_level)
        .bind(tag_id)
        .bind(project_id)
        .execute(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("update tag member failed: {e}")))?;
    Ok(result.rows_affected() > 0)
}

pub async fn revoke_member(
    pool: &DbPool,
    tag_id: &str,
    project_id: &str,
) -> Result<bool, SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE wiki_tag_members SET revoked_at = CURRENT_TIMESTAMP \
         WHERE tag_id = ? AND project_id = ? AND revoked_at IS NULL",
    );
    let result = sqlx::query(&query)
        .bind(tag_id)
        .bind(project_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("revoke tag member failed: {e}")))?;
    Ok(result.rows_affected() > 0)
}

/// Every `(tag, owning project)` pair through which `project_id` reaches another
/// project's pages, with the caller's own access level.
pub async fn shared_links(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<SharedTagLink>, SchedulerError> {
    let rows = sqlx::query(&pool.prepare_query(SHARED_LINKS_SQL))
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list shared tag links failed: {e}")))?;
    Ok(parse_links(&rows))
}

/// `shared_links`, read on a held transaction.
pub async fn shared_links_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<SharedTagLink>, SchedulerError> {
    let rows = sqlx::query(&pool.prepare_query(SHARED_LINKS_SQL))
        .bind(project_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list shared tag links failed: {e}")))?;
    Ok(parse_links(&rows))
}

fn parse_links(rows: &[sqlx::any::AnyRow]) -> Vec<SharedTagLink> {
    rows.iter()
        .map(|r| SharedTagLink {
            tag_id: r.get("tag_id"),
            tag_name: r.get("tag_name"),
            owner_project_id: r.get("owner_project_id"),
            access_level: r.get("access_level"),
        })
        .collect()
}

const SHARED_LINKS_SQL: &str = "SELECT t.id as tag_id, t.name as tag_name, theirs.project_id as owner_project_id, \
         mine.access_level as access_level \
         FROM wiki_tag_members mine \
         JOIN wiki_tag_members theirs \
           ON theirs.tag_id = mine.tag_id \
          AND theirs.project_id <> mine.project_id \
          AND theirs.revoked_at IS NULL \
         JOIN wiki_tags t ON t.id = mine.tag_id \
         JOIN projects owner_project \
           ON owner_project.id = theirs.project_id AND owner_project.deleted_at IS NULL \
         JOIN projects caller_project \
           ON caller_project.id = mine.project_id AND caller_project.deleted_at IS NULL \
         WHERE mine.project_id = ? AND mine.revoked_at IS NULL";

/// Tags `project_id` is an active member of, with its access level in each.
pub async fn memberships_of_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<(String, String, String)>, SchedulerError> {
    let rows = sqlx::query(&pool.prepare_query(MEMBERSHIPS_OF_SQL))
        .bind(project_id)
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list project memberships failed: {e}")))?;
    Ok(rows
        .iter()
        .map(|r| (r.get("tag_id"), r.get("tag_name"), r.get("access_level")))
        .collect())
}

const MEMBERSHIPS_OF_SQL: &str = "SELECT t.id as tag_id, t.name as tag_name, m.access_level \
         FROM wiki_tag_members m \
         JOIN wiki_tags t ON t.id = m.tag_id \
         JOIN projects p ON p.id = m.project_id AND p.deleted_at IS NULL \
         WHERE m.project_id = ? AND m.revoked_at IS NULL \
         ORDER BY t.name";

/// Every tag id keyed by name, read on a held transaction.
pub async fn tag_ids_by_name_in_tx(
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    pool: &DbPool,
) -> Result<std::collections::HashMap<String, String>, SchedulerError> {
    let rows = sqlx::query(&pool.prepare_query("SELECT id, name FROM wiki_tags"))
        .fetch_all(&mut **tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki tags failed: {e}")))?;
    Ok(rows.iter().map(|r| (r.get("name"), r.get("id"))).collect())
}
