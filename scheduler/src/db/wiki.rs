use chrono::{DateTime, Utc};
use sqlx::Row;
use uuid::Uuid;

use super::helpers::parse_timestamp;
use super::{DbPool, TimestampColumn};
use crate::error::SchedulerError;

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

pub async fn create_page(
    pool: &DbPool,
    id: &str,
    project_id: &str,
    slug: &str,
    title: &str,
    body: &str,
    created_by: Option<&str>,
) -> Result<WikiPage, SchedulerError> {
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
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            let msg = e.to_string();
            if msg.contains("UNIQUE constraint failed") || msg.contains("duplicate key") {
                SchedulerError::Conflict(format!("wiki page slug already exists: {slug}"))
            } else {
                SchedulerError::Database(format!("create wiki page failed: {e}"))
            }
        })?;

    get_page_by_slug(pool, project_id, slug).await
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
    search: Option<&str>,
) -> Result<Vec<WikiPageListItem>, SchedulerError> {
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    // List query omits body for efficiency; search still filters on body via LIKE
    let mut sql = format!(
        "SELECT slug, title, revision_number, updated_by, \
         {updated_fmt} as updated_at \
         FROM wiki_pages WHERE project_id = ? AND deleted_at IS NULL"
    );

    if search.is_some() {
        sql.push_str(" AND (LOWER(title) LIKE ? ESCAPE '\\' OR LOWER(body) LIKE ? ESCAPE '\\')");
    }

    sql.push_str(" ORDER BY updated_at DESC");

    let prepared = pool.prepare_query(&sql);
    let mut q = sqlx::query(&prepared);
    q = q.bind(project_id);

    if let Some(term) = search {
        let escaped = term
            .replace('\\', "\\\\")
            .replace('%', "\\%")
            .replace('_', "\\_");
        let pattern = format!("%{}%", escaped.to_lowercase());
        q = q.bind(pattern.clone());
        q = q.bind(pattern);
    }

    let rows = q
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("list wiki pages failed: {e}")))?;

    rows.into_iter().map(parse_wiki_page_list_row).collect()
}

/// OCC update: archive current state to revisions, then update page.
///
/// The `summary` describes the transition from the archived revision to the new content.
/// Returns the updated page, or an error if:
/// - Page not found (NotFound)
/// - Revision mismatch (Conflict)
#[allow(clippy::too_many_arguments)]
pub async fn update_page(
    pool: &DbPool,
    project_id: &str,
    slug: &str,
    title: &str,
    body: &str,
    expected_revision: i64,
    updated_by: Option<&str>,
    summary: Option<&str>,
) -> Result<WikiPage, SchedulerError> {
    let revision_id = Uuid::new_v4().to_string();

    // Step 1: Archive current page state to revisions via INSERT-SELECT.
    // The WHERE clause includes revision_number check for OCC guard.
    let archive_sql = pool.prepare_query(
        "INSERT INTO wiki_page_revisions (id, page_id, revision_number, title, body, summary, created_by, created_at) \
         SELECT ?, id, revision_number, title, body, ?, ?, CURRENT_TIMESTAMP \
         FROM wiki_pages \
         WHERE project_id = ? AND slug = ? AND revision_number = ? AND deleted_at IS NULL"
    );

    let archive_result = sqlx::query(&archive_sql)
        .bind(&revision_id)
        .bind(summary)
        .bind(updated_by)
        .bind(project_id)
        .bind(slug)
        .bind(expected_revision)
        .execute(pool.as_ref())
        .await
        .map_err(|e| {
            let msg = e.to_string();
            // Concurrent archive race: unique index on (page_id, revision_number)
            if msg.contains("UNIQUE constraint failed")
                || msg.contains("duplicate key value violates unique constraint")
            {
                SchedulerError::Conflict("concurrent wiki page update".into())
            } else {
                SchedulerError::Database(format!("archive wiki page revision failed: {e}"))
            }
        })?;

    if archive_result.rows_affected() == 0 {
        // Either page doesn't exist or revision mismatch — differentiate
        return match get_page_by_slug(pool, project_id, slug).await {
            Ok(_) => Err(SchedulerError::Conflict(
                "wiki page revision conflict".into(),
            )),
            Err(SchedulerError::NotFound(_)) => Err(SchedulerError::NotFound(format!(
                "wiki page not found: {slug}"
            ))),
            Err(e) => Err(e),
        };
    }

    // Step 2: Update page with OCC guard
    let update_sql = pool.prepare_query(
        "UPDATE wiki_pages SET title = ?, body = ?, revision_number = revision_number + 1, \
         updated_by = ?, updated_at = CURRENT_TIMESTAMP \
         WHERE project_id = ? AND slug = ? AND revision_number = ? AND deleted_at IS NULL",
    );

    let update_result = sqlx::query(&update_sql)
        .bind(title)
        .bind(body)
        .bind(updated_by)
        .bind(project_id)
        .bind(slug)
        .bind(expected_revision)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("update wiki page failed: {e}")))?;

    if update_result.rows_affected() == 0 {
        // Concurrent update between archive and update (race)
        return Err(SchedulerError::Conflict(
            "concurrent wiki page update".into(),
        ));
    }

    get_page_by_slug(pool, project_id, slug).await
}

pub async fn delete_page(
    pool: &DbPool,
    project_id: &str,
    slug: &str,
) -> Result<(), SchedulerError> {
    let query = pool.prepare_query(
        "UPDATE wiki_pages SET deleted_at = CURRENT_TIMESTAMP \
         WHERE project_id = ? AND slug = ? AND deleted_at IS NULL",
    );

    let result = sqlx::query(&query)
        .bind(project_id)
        .bind(slug)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete wiki page failed: {e}")))?;

    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!(
            "wiki page not found: {slug}"
        )));
    }

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
