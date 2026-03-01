-- Wiki pages: project-scoped, slug-addressed, OCC via revision_number
CREATE TABLE wiki_pages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    revision_number INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    updated_by TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

-- Unique slug per project (only non-deleted pages)
CREATE UNIQUE INDEX idx_wiki_pages_project_slug
    ON wiki_pages (project_id, slug)
    WHERE deleted_at IS NULL;

-- List pages by project (covers WHERE project_id = ? AND deleted_at IS NULL)
CREATE INDEX idx_wiki_pages_project_id ON wiki_pages (project_id, deleted_at);

-- Wiki page revisions: full snapshots, immutable audit trail
CREATE TABLE wiki_page_revisions (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES wiki_pages(id),
    revision_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    summary TEXT,
    created_by TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Look up revisions by page
CREATE INDEX idx_wiki_revisions_page_id ON wiki_page_revisions (page_id);

-- Ensure unique revision number per page
CREATE UNIQUE INDEX idx_wiki_revisions_page_rev
    ON wiki_page_revisions (page_id, revision_number);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (9, CURRENT_TIMESTAMP);
