-- Migration 0016: Add base_commit_sha to sessions
-- Stores the initial HEAD SHA when a worktree is created,
-- used as default base ref for diff endpoint.

ALTER TABLE sessions ADD COLUMN base_commit_sha TEXT;

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (16, CURRENT_TIMESTAMP);
