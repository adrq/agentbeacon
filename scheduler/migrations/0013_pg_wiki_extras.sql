-- Wiki tags, page-tag associations, and subscriptions (PostgreSQL)

CREATE TABLE IF NOT EXISTS wiki_tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS wiki_page_tags (
    page_id TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES wiki_tags(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_wiki_page_tags_tag_id ON wiki_page_tags (tag_id);

CREATE TABLE IF NOT EXISTS wiki_subscriptions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    subscriber TEXT NOT NULL,
    page_slug TEXT,
    tag_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((page_slug IS NOT NULL AND tag_name IS NULL) OR (page_slug IS NULL AND tag_name IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wiki_subscriptions_page
    ON wiki_subscriptions (project_id, subscriber, page_slug)
    WHERE page_slug IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_wiki_subscriptions_tag
    ON wiki_subscriptions (project_id, subscriber, tag_name)
    WHERE tag_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wiki_subscriptions_project
    ON wiki_subscriptions (project_id);

INSERT INTO schema_migrations (version, applied_at)
VALUES (13, CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;
