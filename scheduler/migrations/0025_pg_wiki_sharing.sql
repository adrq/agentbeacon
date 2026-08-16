-- 0025: Share tag membership and project slugs (PostgreSQL).

CREATE TABLE wiki_tag_members (
    id TEXT PRIMARY KEY NOT NULL,
    tag_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    access_level TEXT NOT NULL
        CHECK (access_level IN ('read', 'read_write')),
    created_by TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP,
    FOREIGN KEY (tag_id) REFERENCES wiki_tags(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE UNIQUE INDEX idx_wiki_tag_members_edge ON wiki_tag_members (tag_id, project_id) WHERE revoked_at IS NULL;

CREATE INDEX idx_wiki_tag_members_project_active ON wiki_tag_members (project_id, tag_id) WHERE revoked_at IS NULL;

ALTER TABLE projects ADD COLUMN slug TEXT;

CREATE UNIQUE INDEX idx_projects_slug_active ON projects (slug) WHERE deleted_at IS NULL;

INSERT INTO schema_migrations (version, applied_at) VALUES (25, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
