ALTER TABLE sessions ADD COLUMN desired TEXT NOT NULL DEFAULT 'run';
ALTER TABLE sessions ADD COLUMN executor_state TEXT NOT NULL DEFAULT 'unassigned';
ALTER TABLE sessions ADD COLUMN outcome TEXT;
ALTER TABLE sessions ADD COLUMN desired_by TEXT;
ALTER TABLE sessions ADD COLUMN desired_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN command_token TEXT;
ALTER TABLE sessions ADD COLUMN command_type TEXT;
ALTER TABLE sessions ADD COLUMN command_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN command_has_payload BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sessions ADD COLUMN worker_id TEXT;
ALTER TABLE sessions ADD COLUMN parent_notified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sessions ADD COLUMN continued_from_session_id TEXT REFERENCES sessions(id);

UPDATE sessions SET desired = 'run', executor_state = 'unassigned' WHERE status = 'submitted';
UPDATE sessions SET desired = 'run', executor_state = 'running' WHERE status = 'working';
UPDATE sessions SET desired = 'run', executor_state = 'idle' WHERE status = 'input-required';
UPDATE sessions SET desired = 'terminate', executor_state = 'idle', outcome = 'completed' WHERE status = 'completed';
UPDATE sessions SET desired = 'terminate', executor_state = 'crashed', outcome = 'failed' WHERE status = 'failed';
UPDATE sessions SET desired = 'terminate', executor_state = 'unassigned', outcome = 'canceled' WHERE status = 'canceled';

ALTER TABLE sessions ADD CONSTRAINT sessions_desired_check CHECK (desired IN ('run', 'stop', 'terminate'));
ALTER TABLE sessions ADD CONSTRAINT sessions_executor_state_check CHECK (executor_state IN ('unassigned', 'running', 'idle', 'crashed'));
ALTER TABLE sessions ADD CONSTRAINT sessions_outcome_check CHECK (outcome IS NULL OR outcome IN ('completed', 'canceled', 'failed'));
ALTER TABLE sessions ADD CONSTRAINT sessions_command_type_check CHECK (command_type IS NULL OR command_type IN ('assign', 'feed_turn', 'stop_turn', 'cancel'));

ALTER TABLE sessions DROP COLUMN status;
ALTER TABLE sessions DROP COLUMN last_progress_at;

DROP INDEX IF EXISTS idx_sessions_status;
CREATE INDEX idx_sessions_desired_executor_state ON sessions(desired, executor_state);
CREATE INDEX idx_sessions_worker_id ON sessions(worker_id);

ALTER TABLE executions ADD COLUMN desired TEXT NOT NULL DEFAULT 'run';
ALTER TABLE executions ADD COLUMN outcome TEXT;

UPDATE executions SET desired = 'run' WHERE status IN ('submitted', 'working', 'input-required');
UPDATE executions SET desired = 'terminate', outcome = 'completed' WHERE status = 'completed';
UPDATE executions SET desired = 'terminate', outcome = 'failed' WHERE status = 'failed';
UPDATE executions SET desired = 'terminate', outcome = 'canceled' WHERE status = 'canceled';

ALTER TABLE executions ADD CONSTRAINT executions_desired_check CHECK (desired IN ('run', 'terminate'));
ALTER TABLE executions ADD CONSTRAINT executions_outcome_check CHECK (outcome IS NULL OR outcome IN ('completed', 'canceled', 'failed'));

ALTER TABLE executions DROP COLUMN status;
DROP INDEX IF EXISTS idx_executions_status;

ALTER TABLE task_queue ADD COLUMN source TEXT;

ALTER TABLE events DROP CONSTRAINT IF EXISTS events_event_type_check;
ALTER TABLE events ADD CONSTRAINT events_event_type_check CHECK (event_type IN ('message', 'state_change', 'platform', 'escalate'));

INSERT INTO schema_migrations (version, applied_at) VALUES (19, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
