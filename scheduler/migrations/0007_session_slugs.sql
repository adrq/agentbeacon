-- Add slug column for hierarchical naming (Docker-style adjective-noun)
ALTER TABLE sessions ADD COLUMN slug TEXT NOT NULL DEFAULT '';

-- Enforce slug uniqueness among siblings at the DB level.
-- Partial index: only enforce for non-empty slugs (pre-migration rows have empty slug).
-- execution_id scopes root sessions (NULL parent) per-execution, not globally.
-- COALESCE handles NULL parent_session_id — SQLite treats each NULL as distinct.
CREATE UNIQUE INDEX idx_sessions_parent_slug ON sessions (execution_id, COALESCE(parent_session_id, ''), slug) WHERE slug != '';

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (7, CURRENT_TIMESTAMP);
