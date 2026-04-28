-- 0022: Add sandbox_policy column to executions and sessions.
-- SQLite: recreate tables to enforce NOT NULL.

-- === Executions: add sandbox_policy ===

ALTER TABLE executions ADD COLUMN sandbox_policy TEXT;

UPDATE executions SET sandbox_policy = '{"fs_level":"unrestricted"}' WHERE sandbox_policy IS NULL;

CREATE TABLE executions_new (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_execution_id TEXT,
    context_id TEXT NOT NULL,
    desired TEXT NOT NULL DEFAULT 'run'
        CHECK (desired IN ('run', 'terminate')),
    outcome TEXT
        CHECK (outcome IS NULL OR outcome IN ('completed', 'canceled', 'failed')),
    title TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    max_depth INTEGER NOT NULL DEFAULT 2,
    max_width INTEGER NOT NULL DEFAULT 5,
    sandbox_policy TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_execution_id) REFERENCES executions_new(id) ON DELETE CASCADE
);

INSERT INTO executions_new (id, project_id, parent_execution_id, context_id, desired, outcome,
    title, metadata, max_depth, max_width, sandbox_policy, created_at, updated_at, completed_at)
SELECT id, project_id, parent_execution_id, context_id, desired, outcome,
    title, metadata, max_depth, max_width, sandbox_policy, created_at, updated_at, completed_at
FROM executions;

DROP TABLE executions;

ALTER TABLE executions_new RENAME TO executions;

CREATE INDEX idx_executions_project_id ON executions(project_id);
CREATE INDEX idx_executions_context_id ON executions(context_id);
CREATE INDEX idx_executions_created_at ON executions(created_at);

-- === Sessions: add sandbox_policy ===

ALTER TABLE sessions ADD COLUMN sandbox_policy TEXT;

UPDATE sessions SET sandbox_policy = '{"fs_level":"unrestricted"}' WHERE sandbox_policy IS NULL;

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
    sandbox_policy TEXT NOT NULL,
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
    desired, executor_state, outcome, desired_by, desired_at,
    command_token, command_type, command_at, command_has_payload,
    worker_id, parent_notified, continued_from_session_id, sandbox_policy,
    created_at, updated_at, completed_at)
SELECT id, execution_id, parent_session_id, agent_id, agent_session_id,
    slug, cwd, worktree_path, base_commit_sha, recovery_attempts, metadata,
    desired, executor_state, outcome, desired_by, desired_at,
    command_token, command_type, command_at, command_has_payload,
    worker_id, parent_notified, continued_from_session_id, sandbox_policy,
    created_at, updated_at, completed_at
FROM sessions;

DROP TABLE sessions;

ALTER TABLE sessions_new RENAME TO sessions;

CREATE INDEX idx_sessions_execution_id ON sessions(execution_id);
CREATE INDEX idx_sessions_parent_session_id ON sessions(parent_session_id);
CREATE UNIQUE INDEX idx_sessions_slug_unique ON sessions(execution_id, parent_session_id, slug)
    WHERE slug != '';
CREATE UNIQUE INDEX idx_sessions_parent_slug ON sessions(execution_id, COALESCE(parent_session_id, ''), slug) WHERE slug != '';
CREATE INDEX idx_sessions_desired_executor_state ON sessions(desired, executor_state);
CREATE INDEX idx_sessions_worker_id ON sessions(worker_id);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (22, CURRENT_TIMESTAMP);
