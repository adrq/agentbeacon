-- 0027: Enforce projects.slug NOT NULL.
-- Every row was given a slug by the 0026 code step.
-- SQLite has no ALTER COLUMN, so the table is rebuilt. The runner toggles
-- foreign_keys off around this version: projects has inbound references from
-- artifacts, executions, project_agents, project_mcp_servers, wiki_pages,
-- wiki_subscriptions and wiki_tag_members, and DROP TABLE with foreign_keys on
-- deletes child rows.
-- Columns are listed explicitly, and every index on projects is recreated.

CREATE TABLE projects_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    slug TEXT NOT NULL
);

INSERT INTO projects_new (id, name, path, settings, deleted_at, created_at, updated_at, slug)
    SELECT id, name, path, settings, deleted_at, created_at, updated_at, slug FROM projects;

DROP TABLE projects;

ALTER TABLE projects_new RENAME TO projects;

CREATE INDEX idx_projects_path ON projects(path);

CREATE UNIQUE INDEX idx_projects_slug_active ON projects (slug) WHERE deleted_at IS NULL;

-- Abort the migration if the rebuild left a dangling reference. PRAGMA
-- foreign_key_check only reports rows, and the runner discards result rows, so
-- the report is turned into a failure: the guard column is NOT NULL with a
-- CHECK that only NULL satisfies, so it accepts no row at all. A clean database
-- selects nothing and inserts nothing; one violation raises and rolls back.
CREATE TABLE projects_rebuild_guard (violation TEXT NOT NULL CHECK (violation IS NULL));

INSERT INTO projects_rebuild_guard (violation) SELECT '' FROM pragma_foreign_key_check LIMIT 1;

DROP TABLE projects_rebuild_guard;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (27, CURRENT_TIMESTAMP);
