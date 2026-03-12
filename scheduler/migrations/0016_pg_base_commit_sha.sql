-- Migration 0016: Add base_commit_sha to sessions (PostgreSQL)

ALTER TABLE sessions ADD COLUMN base_commit_sha TEXT;

INSERT INTO schema_migrations (version, applied_at)
VALUES (16, CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;
