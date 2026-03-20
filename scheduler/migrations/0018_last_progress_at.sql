-- 0018: Add last_progress_at column to sessions
-- Separates real progress (turn results, session claims) from heartbeat liveness
-- (updated_at). Prevents stuck sessions from being masked by fresh heartbeats.
-- SQLite requires constant defaults for ALTER TABLE ADD COLUMN, so we use NULL
-- and immediately backfill from updated_at.

ALTER TABLE sessions ADD COLUMN last_progress_at TIMESTAMP;
UPDATE sessions SET last_progress_at = COALESCE(updated_at, CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (18, CURRENT_TIMESTAMP);
