-- AgentBeacon target schema: master-agent coordination model
-- Replaces static DAG workflow model with A2A-aligned execution tracking

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Global key-value configuration
CREATE TABLE IF NOT EXISTS config (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Agent registry
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    agent_type TEXT NOT NULL CHECK (agent_type IN ('claude_sdk', 'codex_sdk', 'copilot_sdk', 'opencode_sdk', 'acp', 'a2a')),
    config TEXT NOT NULL,
    sandbox_config TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_agents_enabled ON agents(enabled);

-- Project workspaces
CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    default_agent_id TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (default_agent_id) REFERENCES agents(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX idx_workspaces_project_path ON workspaces(project_path);

-- Executions (A2A Tasks)
CREATE TABLE executions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    parent_execution_id TEXT,
    context_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'working', 'input-required', 'completed', 'failed', 'canceled')),
    title TEXT,
    input TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_execution_id) REFERENCES executions(id) ON DELETE CASCADE
);

CREATE INDEX idx_executions_workspace_id ON executions(workspace_id);
CREATE INDEX idx_executions_status ON executions(status);
CREATE INDEX idx_executions_context_id ON executions(context_id);
CREATE INDEX idx_executions_created_at ON executions(created_at);

-- Sessions (agent conversations within executions)
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    parent_session_id TEXT,
    agent_id TEXT NOT NULL,
    agent_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'working', 'input-required', 'completed', 'failed', 'canceled')),
    coordination_mode TEXT NOT NULL DEFAULT 'sdk'
        CHECK (coordination_mode IN ('sdk', 'mcp_poll')),
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE INDEX idx_sessions_execution_id ON sessions(execution_id);
CREATE INDEX idx_sessions_status ON sessions(status);

-- Unified event log
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    session_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN ('message', 'state_change')),
    payload TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_events_execution_id ON events(execution_id);
CREATE INDEX idx_events_session_id ON events(session_id);
CREATE INDEX idx_events_execution_timestamp ON events(execution_id, created_at);
CREATE INDEX idx_events_session_timestamp ON events(session_id, created_at);

-- Artifacts (file refs, commits, URLs produced by agents)
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    session_id TEXT,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('file', 'commit', 'url')),
    name TEXT NOT NULL,
    description TEXT,
    reference TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE INDEX idx_artifacts_workspace_id ON artifacts(workspace_id);
CREATE INDEX idx_artifacts_session_id ON artifacts(session_id);

-- Task queue (work dispatch for workers, FIFO)
CREATE TABLE task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_payload TEXT NOT NULL,
    queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_task_queue_queued ON task_queue(queued_at ASC);
CREATE INDEX idx_task_queue_session_queued ON task_queue(session_id, queued_at ASC);

-- Record migration
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
