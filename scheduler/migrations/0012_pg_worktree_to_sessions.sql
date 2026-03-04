-- Migration 0012: Move worktree_path from executions to sessions (D16 compliance)

ALTER TABLE sessions ADD COLUMN worktree_path TEXT;

UPDATE sessions SET worktree_path = e.worktree_path
FROM executions e
WHERE e.id = sessions.execution_id
AND sessions.parent_session_id IS NULL
AND e.worktree_path IS NOT NULL;

ALTER TABLE executions DROP COLUMN worktree_path;

INSERT INTO schema_migrations (version, applied_at) VALUES (12, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
