CREATE TABLE sessions_new (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    parent_session_id TEXT,
    agent_id TEXT NOT NULL,
    agent_session_id TEXT,
    slug TEXT NOT NULL DEFAULT '',
    cwd TEXT,
    worktree_path TEXT,
    base_commit_sha TEXT,
    recovery_attempts INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    desired TEXT NOT NULL DEFAULT 'run'
        CHECK (desired IN ('run', 'stop', 'terminate')),
    executor_state TEXT NOT NULL DEFAULT 'unassigned'
        CHECK (executor_state IN ('unassigned', 'running', 'idle', 'crashed')),
    outcome TEXT
        CHECK (outcome IS NULL OR outcome IN ('completed', 'canceled', 'failed')),
    desired_by TEXT,
    desired_at TIMESTAMP,
    command_token TEXT,
    command_type TEXT
        CHECK (command_type IS NULL OR command_type IN ('assign', 'feed_turn', 'stop_turn', 'cancel')),
    command_at TIMESTAMP,
    command_has_payload BOOLEAN NOT NULL DEFAULT FALSE,
    worker_id TEXT,
    parent_notified BOOLEAN NOT NULL DEFAULT FALSE,
    continued_from_session_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_session_id) REFERENCES sessions_new(id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents(id),
    FOREIGN KEY (continued_from_session_id) REFERENCES sessions_new(id)
);

INSERT INTO sessions_new (id, execution_id, parent_session_id, agent_id, agent_session_id,
    slug, cwd, worktree_path, base_commit_sha, recovery_attempts, metadata,
    desired, executor_state, outcome,
    created_at, updated_at, completed_at)
SELECT id, execution_id, parent_session_id, agent_id, agent_session_id,
    slug, cwd, worktree_path, base_commit_sha, recovery_attempts, metadata,
    CASE status
        WHEN 'submitted' THEN 'run'
        WHEN 'working' THEN 'run'
        WHEN 'input-required' THEN 'run'
        WHEN 'completed' THEN 'terminate'
        WHEN 'failed' THEN 'terminate'
        WHEN 'canceled' THEN 'terminate'
    END,
    CASE status
        WHEN 'submitted' THEN 'unassigned'
        WHEN 'working' THEN 'running'
        WHEN 'input-required' THEN 'idle'
        WHEN 'completed' THEN 'idle'
        WHEN 'failed' THEN 'crashed'
        WHEN 'canceled' THEN 'unassigned'
    END,
    CASE status
        WHEN 'completed' THEN 'completed'
        WHEN 'failed' THEN 'failed'
        WHEN 'canceled' THEN 'canceled'
        ELSE NULL
    END,
    created_at, updated_at, completed_at
FROM sessions;

DROP TABLE sessions;

ALTER TABLE sessions_new RENAME TO sessions;

CREATE INDEX idx_sessions_execution_id ON sessions(execution_id);
CREATE UNIQUE INDEX idx_sessions_parent_slug ON sessions(execution_id, COALESCE(parent_session_id, ''), slug) WHERE slug != '';
CREATE INDEX idx_sessions_desired_executor_state ON sessions(desired, executor_state);
CREATE INDEX idx_sessions_worker_id ON sessions(worker_id);

CREATE TABLE executions_new (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_execution_id TEXT,
    context_id TEXT NOT NULL,
    title TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    max_depth INTEGER NOT NULL DEFAULT 2,
    max_width INTEGER NOT NULL DEFAULT 5,
    desired TEXT NOT NULL DEFAULT 'run'
        CHECK (desired IN ('run', 'terminate')),
    outcome TEXT
        CHECK (outcome IS NULL OR outcome IN ('completed', 'canceled', 'failed')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_execution_id) REFERENCES executions_new(id) ON DELETE CASCADE
);

INSERT INTO executions_new (id, project_id, parent_execution_id, context_id,
    title, metadata, max_depth, max_width, desired, outcome,
    created_at, updated_at, completed_at)
SELECT id, project_id, parent_execution_id, context_id,
    title, metadata, max_depth, max_width,
    CASE status
        WHEN 'submitted' THEN 'run'
        WHEN 'working' THEN 'run'
        WHEN 'input-required' THEN 'run'
        WHEN 'completed' THEN 'terminate'
        WHEN 'failed' THEN 'terminate'
        WHEN 'canceled' THEN 'terminate'
    END,
    CASE status
        WHEN 'completed' THEN 'completed'
        WHEN 'failed' THEN 'failed'
        WHEN 'canceled' THEN 'canceled'
        ELSE NULL
    END,
    created_at, updated_at, completed_at
FROM executions;

DROP TABLE executions;

ALTER TABLE executions_new RENAME TO executions;

CREATE INDEX idx_executions_project_id ON executions(project_id);
CREATE INDEX idx_executions_context_id ON executions(context_id);
CREATE INDEX idx_executions_created_at ON executions(created_at);

ALTER TABLE task_queue ADD COLUMN source TEXT;

CREATE TABLE events_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    session_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN ('message', 'state_change', 'platform', 'escalate')),
    payload TEXT NOT NULL,
    msg_seq INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

INSERT INTO events_new (id, execution_id, session_id, event_type, payload, msg_seq, created_at)
    SELECT id, execution_id, session_id, event_type, payload, msg_seq, created_at FROM events;

DROP TABLE events;
ALTER TABLE events_new RENAME TO events;

CREATE INDEX idx_events_execution_id ON events(execution_id);
CREATE INDEX idx_events_session_id ON events(session_id);
CREATE INDEX idx_events_execution_timestamp ON events(execution_id, created_at);
CREATE INDEX idx_events_session_timestamp ON events(session_id, created_at);
CREATE UNIQUE INDEX idx_events_session_msg_seq ON events(session_id, msg_seq);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (19, CURRENT_TIMESTAMP);
