-- Migration 0014: Dynamic agent briefing
-- Adds system_prompt column to agents, drops default_agent_id from projects,
-- creates project_agents junction table, seeds briefing config.

-- Add system_prompt to agents (separate from config JSON)
ALTER TABLE agents ADD COLUMN system_prompt TEXT;

-- Drop default_agent_id from projects (superseded by project_agents pool).
-- SQLite can't ALTER TABLE DROP COLUMN, so we recreate.
-- FK refs from executions/wiki_pages/wiki_subscriptions require PRAGMA foreign_keys=OFF
-- (handled by migration runner's needs_fk_disable for version 14).
CREATE TABLE projects_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO projects_new (id, name, path, settings, deleted_at, created_at, updated_at)
    SELECT id, name, path, settings, deleted_at, created_at, updated_at FROM projects;
DROP TABLE projects;
ALTER TABLE projects_new RENAME TO projects;

-- Recreate index lost during table recreation
CREATE INDEX IF NOT EXISTS idx_projects_path ON projects(path);

-- Project agent pool junction table (AFTER projects recreation)
CREATE TABLE IF NOT EXISTS project_agents (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    PRIMARY KEY (project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_project_agents_agent_id ON project_agents(agent_id);

-- Seed framework briefing sections into config table
INSERT OR IGNORE INTO config (name, value) VALUES
    ('briefing.delegation', 'Use the AgentBeacon `delegate` MCP tool to assign work to child agents.
Use the AgentBeacon `release` MCP tool to terminate a child when done.
Discover available agent configs via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/agents` before delegating.
An **agent** is a configured specialist type (e.g., `backend-dev`). A **session** is a running instance — delegating to the same agent twice creates two independent sessions.');

INSERT OR IGNORE INTO config (name, value) VALUES
    ('briefing.escalate', 'Use the AgentBeacon `escalate` MCP tool to surface questions to the user.');

INSERT OR IGNORE INTO config (name, value) VALUES
    ('briefing.rest_api', 'Environment variables for API access:
- `$AGENTBEACON_SESSION_ID` — your auth token
- `$AGENTBEACON_API_BASE` — scheduler base URL
- `$AGENTBEACON_EXECUTION_ID` — current execution
- `$AGENTBEACON_PROJECT_ID` — current project (if set)

`GET $AGENTBEACON_API_BASE/api/docs` for the full API reference.
Discover running sessions via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions`.
You have a REST API for coordinating with other agents — send messages to peers, read/write shared knowledge in the wiki, and discover who else is working in this execution. Write scripts to interact with the API (e.g. discover agents, filter results, send messages in a loop) rather than making one curl call at a time — process data in code, not in your context window.');

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (14, CURRENT_TIMESTAMP);
