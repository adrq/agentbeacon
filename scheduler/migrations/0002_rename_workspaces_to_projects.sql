-- Migration 0002: Rename workspaces to projects, add new columns, recreate FK tables
--
-- Uses recreate-table pattern for tables with FK column renames (SQLite limitation).
-- Migration runner handles PRAGMA foreign_keys OFF/ON around this migration for SQLite.

-- Step 1: Rename workspaces table to projects, rename project_path to path, add deleted_at
ALTER TABLE workspaces RENAME TO projects;
ALTER TABLE projects RENAME COLUMN project_path TO path;
ALTER TABLE projects ADD COLUMN deleted_at TIMESTAMP;

-- Step 2: Recreate executions with project_id (was workspace_id) and worktree_path
CREATE TABLE executions_new (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_execution_id TEXT,
    context_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'working', 'input-required', 'completed', 'failed', 'canceled')),
    title TEXT,
    input TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    worktree_path TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_execution_id) REFERENCES executions_new(id) ON DELETE CASCADE
);

INSERT INTO executions_new (id, project_id, parent_execution_id, context_id, status, title, input, metadata, worktree_path, created_at, updated_at, completed_at)
    SELECT id, workspace_id, parent_execution_id, context_id, status, title, input, metadata, NULL, created_at, updated_at, completed_at FROM executions;

DROP TABLE executions;
ALTER TABLE executions_new RENAME TO executions;

CREATE INDEX idx_executions_project_id ON executions(project_id);
CREATE INDEX idx_executions_status ON executions(status);
CREATE INDEX idx_executions_context_id ON executions(context_id);
CREATE INDEX idx_executions_created_at ON executions(created_at);

-- Step 3: Recreate artifacts with project_id (was workspace_id)
CREATE TABLE artifacts_new (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    session_id TEXT,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('file', 'commit', 'url')),
    name TEXT NOT NULL,
    description TEXT,
    reference TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

INSERT INTO artifacts_new (id, project_id, session_id, artifact_type, name, description, reference, metadata, created_at)
    SELECT id, workspace_id, session_id, artifact_type, name, description, reference, metadata, created_at FROM artifacts;

DROP TABLE artifacts;
ALTER TABLE artifacts_new RENAME TO artifacts;

CREATE INDEX idx_artifacts_project_id ON artifacts(project_id);
CREATE INDEX idx_artifacts_session_id ON artifacts(session_id);

-- Step 4: Recreate agents without UNIQUE on name, add deleted_at, add partial unique index
CREATE TABLE agents_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    agent_type TEXT NOT NULL CHECK (agent_type IN ('claude_sdk', 'codex_sdk', 'copilot_sdk', 'opencode_sdk', 'acp', 'a2a')),
    config TEXT NOT NULL,
    sandbox_config TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    deleted_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO agents_new (id, name, description, agent_type, config, sandbox_config, enabled, deleted_at, created_at, updated_at)
    SELECT id, name, description, agent_type, config, sandbox_config, enabled, NULL, created_at, updated_at FROM agents;

DROP TABLE agents;
ALTER TABLE agents_new RENAME TO agents;

CREATE INDEX idx_agents_enabled ON agents(enabled);
CREATE UNIQUE INDEX idx_agents_name_active ON agents(name) WHERE deleted_at IS NULL;

-- Step 5: Update indexes on projects (was workspaces)
DROP INDEX IF EXISTS idx_workspaces_project_path;
CREATE INDEX idx_projects_path ON projects(path);

-- Step 6: Add cwd to sessions
ALTER TABLE sessions ADD COLUMN cwd TEXT;

-- Record migration
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (2, CURRENT_TIMESTAMP);
