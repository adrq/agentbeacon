-- 0017: Remove redundant input column (prompt stored as first session event)
-- PostgreSQL: simple ALTER TABLE

ALTER TABLE executions DROP COLUMN IF EXISTS input;

INSERT INTO schema_migrations (version, applied_at) VALUES (17, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
