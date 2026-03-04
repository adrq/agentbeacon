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

/// Return all non-deleted wiki pages for a project with full content (for search indexing).
pub async fn list_pages_for_indexing(
    pool: &DbPool,
    project_id: &str,
) -> Result<Vec<WikiPage>, SchedulerError> {
    let created_fmt = pool.format_timestamp(TimestampColumn::CreatedAt);
    let updated_fmt = pool.format_timestamp(TimestampColumn::UpdatedAt);

    let sql = format!(
        "SELECT id, project_id, slug, title, body, revision_number, created_by, updated_by, \
         {created_fmt} as created_at, {updated_fmt} as updated_at \
         FROM wiki_pages WHERE project_id = ? AND deleted_at IS NULL"
    );
    let query = pool.prepare_query(&sql);

    let rows = sqlx::query(&query)
        .bind(project_id)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("list wiki pages for indexing failed: {e}"))
        })?;

    rows.into_iter().map(parse_wiki_page_row).collect()
}

/// Return all project_ids that have at least one non-deleted wiki page.
pub async fn projects_with_pages(pool: &DbPool) -> Result<Vec<String>, SchedulerError> {
    let query =
        pool.prepare_query("SELECT DISTINCT project_id FROM wiki_pages WHERE deleted_at IS NULL");

    let rows = sqlx::query(&query)
        .fetch_all(pool.as_ref())
        .await
        .map_err(|e| {
            SchedulerError::Database(format!("query projects with wiki pages failed: {e}"))
        })?;

    Ok(rows.iter().map(|r| r.get("project_id")).collect())
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

// --- Tags ---

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

// --- Subscriptions ---

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
) -> Result<(WikiSubscription, bool), SchedulerError> {
    let insert_sql = if pool.is_postgres() {
        "INSERT INTO wiki_subscriptions (id, project_id, subscriber, page_slug, tag_name) \
         VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"
    } else {
        "INSERT OR IGNORE INTO wiki_subscriptions (id, project_id, subscriber, page_slug, tag_name) \
         VALUES (?, ?, ?, ?, ?)"
    };
    let insert_query = pool.prepare_query(insert_sql);
    let result = sqlx::query(&insert_query)
        .bind(id)
        .bind(project_id)
        .bind(subscriber)
        .bind(page_slug)
        .bind(tag_name)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("create wiki subscription failed: {e}")))?;

    let was_created = result.rows_affected() > 0;

    // Fetch the subscription — branch based on known target type to avoid NULL comparison
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
        return Err(SchedulerError::ValidationFailed(
            "exactly one of page_slug or tag_name must be provided".into(),
        ));
    };
    let fetch_query = pool.prepare_query(&fetch_sql);
    let row = sqlx::query(&fetch_query)
        .bind(project_id)
        .bind(subscriber)
        .bind(target_value)
        .fetch_one(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("fetch wiki subscription failed: {e}")))?;

    Ok((parse_subscription_row(row)?, was_created))
}

/// Delete a subscription by id, scoped to project.
pub async fn delete_subscription(
    pool: &DbPool,
    project_id: &str,
    id: &str,
) -> Result<(), SchedulerError> {
    let query =
        pool.prepare_query("DELETE FROM wiki_subscriptions WHERE id = ? AND project_id = ?");
    let result = sqlx::query(&query)
        .bind(id)
        .bind(project_id)
        .execute(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("delete wiki subscription failed: {e}")))?;
    if result.rows_affected() == 0 {
        return Err(SchedulerError::NotFound(format!(
            "subscription not found: {id}"
        )));
    }
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

// --- Changes feed ---

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
            // SQLite stores CURRENT_TIMESTAMP as 'YYYY-MM-DD HH:MM:SS'.
            // Clients send RFC3339 (with 'T' and timezone) — normalize both via datetime().
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

// --- Export ---

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
