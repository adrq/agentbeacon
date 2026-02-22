-- Add msg_seq for sequence-based deduplication of mid-turn messages.
-- NULLs are distinct in unique indexes, so state_change/platform events
-- (which have msg_seq = NULL) never conflict.
ALTER TABLE events ADD COLUMN msg_seq INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_session_msg_seq ON events(session_id, msg_seq);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (3, CURRENT_TIMESTAMP);
