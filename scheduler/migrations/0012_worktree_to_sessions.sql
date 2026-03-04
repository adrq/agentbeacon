-- Migration 0012: Move worktree_path from executions to sessions (D16 compliance)

ALTER TABLE sessions ADD COLUMN worktree_path TEXT;

UPDATE sessions SET worktree_path = (
    SELECT e.worktree_path FROM executions e
    WHERE e.id = sessions.execution_id
) WHERE parent_session_id IS NULL
AND EXISTS (
    SELECT 1 FROM executions e
    WHERE e.id = sessions.execution_id AND e.worktree_path IS NOT NULL
);

ALTER TABLE executions DROP COLUMN worktree_path;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (12, CURRENT_TIMESTAMP);
