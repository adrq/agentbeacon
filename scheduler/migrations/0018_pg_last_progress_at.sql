-- 0018: Add last_progress_at column to sessions
-- Separates real progress (turn results, session claims) from heartbeat liveness
-- (updated_at). Prevents stuck sessions from being masked by fresh heartbeats.

ALTER TABLE sessions ADD COLUMN last_progress_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
UPDATE sessions SET last_progress_at = updated_at;

INSERT INTO schema_migrations (version, applied_at) VALUES (18, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
