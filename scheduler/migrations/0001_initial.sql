-- Initial schema for AgentMaestro Scheduler (Phase 3 Week 4)
-- Supports both SQLite and PostgreSQL with identical table structures
-- Source: docs/workflow-schema.json and docs/a2a-v0.3.0.schema.json

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Workflow definitions conforming to docs/workflow-schema.json
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,  -- UUID stored as TEXT for cross-DB compatibility
    name TEXT NOT NULL UNIQUE,  -- Workflow name from YAML (required field)
    description TEXT,  -- Optional workflow description
    yaml_content TEXT NOT NULL,  -- Complete workflow YAML (validated against schema)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workflows_name ON workflows(name);

-- Configuration storage (plain text in Week 4)
CREATE TABLE IF NOT EXISTS config (
    name TEXT PRIMARY KEY,  -- Configuration key
    value TEXT NOT NULL,  -- Configuration value (plain text)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Workflow execution tracking with task state management
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,  -- UUID stored as TEXT
    workflow_id TEXT NOT NULL,  -- References workflows.id
    status TEXT NOT NULL,  -- pending|running|completed|failed|cancelled
    task_states TEXT NOT NULL,  -- JSON object mapping task IDs to states
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,  -- NULL for non-terminal states
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_executions_workflow_id ON executions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_created_at ON executions(created_at);

-- Execution event audit log (append-only)
CREATE TABLE IF NOT EXISTS execution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- Auto-increment event ID
    execution_id TEXT NOT NULL,  -- References executions.id
    event_type TEXT NOT NULL,  -- execution_start|execution_complete|task_start|task_complete|error|info
    task_id TEXT,  -- Optional task identifier from workflow.tasks[].id
    message TEXT NOT NULL,  -- Human-readable event message
    metadata TEXT NOT NULL,  -- JSON object with event metadata
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_execution_events_execution_id ON execution_events(execution_id);
CREATE INDEX IF NOT EXISTS idx_execution_events_timestamp ON execution_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_execution_events_execution_timestamp ON execution_events(execution_id, timestamp);

-- Insert initial schema version
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
