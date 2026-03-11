-- Migration 0014: Dynamic agent briefing (PostgreSQL variant)
-- Adds system_prompt column to agents, drops default_agent_id from projects,
-- creates project_agents junction table, seeds briefing config.

ALTER TABLE agents ADD COLUMN system_prompt TEXT;
ALTER TABLE projects DROP COLUMN IF EXISTS default_agent_id;

CREATE TABLE IF NOT EXISTS project_agents (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    PRIMARY KEY (project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_project_agents_agent_id ON project_agents(agent_id);

INSERT INTO config (name, value, created_at, updated_at) VALUES
    ('briefing.delegation', 'Use the AgentBeacon `delegate` MCP tool to assign work to child agents.
Use the AgentBeacon `release` MCP tool to terminate a child when done.
Discover available agent configs via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/agents` before delegating.
An **agent** is a configured specialist type (e.g., `backend-dev`). A **session** is a running instance — delegating to the same agent twice creates two independent sessions.',
    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT (name) DO NOTHING;

INSERT INTO config (name, value, created_at, updated_at) VALUES
    ('briefing.escalate', 'Use the AgentBeacon `escalate` MCP tool to surface questions to the user.',
    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT (name) DO NOTHING;

INSERT INTO config (name, value, created_at, updated_at) VALUES
    ('briefing.rest_api', 'Environment variables for API access:
- `$AGENTBEACON_SESSION_ID` — your auth token
- `$AGENTBEACON_API_BASE` — scheduler base URL
- `$AGENTBEACON_EXECUTION_ID` — current execution
- `$AGENTBEACON_PROJECT_ID` — current project (if set)

`GET $AGENTBEACON_API_BASE/api/docs` for the full API reference.
Discover running sessions via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions`.
You have a REST API for coordinating with other agents — send messages to peers, read/write shared knowledge in the wiki, and discover who else is working in this execution. Write scripts to interact with the API (e.g. discover agents, filter results, send messages in a loop) rather than making one curl call at a time — process data in code, not in your context window.',
    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (version, applied_at) VALUES (14, CURRENT_TIMESTAMP)
    ON CONFLICT (version) DO NOTHING;
