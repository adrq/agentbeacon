-- Events index changes.

CREATE INDEX IF NOT EXISTS idx_events_execution_id_id ON events(execution_id, id);
CREATE INDEX IF NOT EXISTS idx_events_session_id_id ON events(session_id, id);

CREATE INDEX IF NOT EXISTS idx_events_platform_id ON events(id) WHERE event_type = 'platform';
CREATE INDEX IF NOT EXISTS idx_events_execution_platform_id ON events(execution_id, id) WHERE event_type = 'platform';

DROP INDEX IF EXISTS idx_events_execution_id;
DROP INDEX IF EXISTS idx_events_session_id;
DROP INDEX IF EXISTS idx_events_execution_timestamp;
DROP INDEX IF EXISTS idx_events_session_timestamp;

INSERT INTO schema_migrations (version, applied_at) VALUES (28, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
