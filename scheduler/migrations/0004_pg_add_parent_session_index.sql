-- Index on parent_session_id for recursive CTE performance (get_subtree).
CREATE INDEX IF NOT EXISTS idx_sessions_parent_session_id ON sessions(parent_session_id);

INSERT INTO schema_migrations (version, applied_at)
VALUES (4, CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;
