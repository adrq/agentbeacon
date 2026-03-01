-- Fix slug uniqueness index: scope root sessions per-execution (not globally).
-- The original index on (COALESCE(parent_session_id, ''), slug) caused root sessions
-- across different executions to collide on slugs.
DROP INDEX IF EXISTS idx_sessions_parent_slug;
CREATE UNIQUE INDEX idx_sessions_parent_slug ON sessions (execution_id, COALESCE(parent_session_id, ''), slug) WHERE slug != '';

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (8, CURRENT_TIMESTAMP);
