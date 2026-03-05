-- Migration 0005: Data model split (Drivers / Agents / Sessions)
-- Introduces drivers table, adds driver_id FK to agents, creates execution_agents junction.

-- 1. Create drivers table (hard delete, no deleted_at)
CREATE TABLE drivers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL UNIQUE,
    config TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Seed drivers with stable UUIDs (product constants)
INSERT INTO drivers (id, name, platform, config) VALUES
    ('ff5b1509-ac29-4957-80b7-6e8aaca69e08', 'acp', 'acp', '{}'),
    ('808fcfca-8ba7-42ce-a02e-b6b159a100c6', 'a2a', 'a2a', '{}'),
    ('a7315f6e-6559-4569-a603-6dbc320c0d0f', 'claude_sdk', 'claude_sdk', '{}'),
    ('dbd14a23-310a-4de5-a3d9-cb2083eed4cc', 'codex_sdk', 'codex_sdk', '{}'),
    ('4fde05d2-e929-438c-9cf0-d8ffc3e2240c', 'copilot_sdk', 'copilot_sdk', '{}'),
    ('224d5c95-1e15-4cf1-9230-dce0519ef240', 'opencode_sdk', 'opencode_sdk', '{}');

-- 3. Recreate agents with driver_id FK (SQLite can't ALTER ADD FK)
CREATE TABLE agents_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    agent_type TEXT NOT NULL,
    driver_id TEXT,
    config TEXT NOT NULL,
    sandbox_config TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    deleted_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (driver_id) REFERENCES drivers(id)
);

INSERT INTO agents_new (id, name, description, agent_type, driver_id, config,
                        sandbox_config, enabled, deleted_at, created_at, updated_at)
SELECT a.id, a.name, a.description, a.agent_type, d.id,
       a.config, a.sandbox_config, a.enabled, a.deleted_at, a.created_at, a.updated_at
FROM agents a
LEFT JOIN drivers d ON d.platform = a.agent_type;

DROP TABLE agents;
ALTER TABLE agents_new RENAME TO agents;

CREATE INDEX idx_agents_enabled ON agents(enabled);
CREATE UNIQUE INDEX idx_agents_name_active ON agents(name) WHERE deleted_at IS NULL;
CREATE INDEX idx_agents_driver_id ON agents(driver_id);

-- 4. Create execution_agents junction table
CREATE TABLE execution_agents (
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    PRIMARY KEY (execution_id, agent_id),
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);
CREATE INDEX idx_execution_agents_agent_id ON execution_agents(agent_id);

-- 5. Backfill junction from existing sessions
INSERT OR IGNORE INTO execution_agents (execution_id, agent_id)
SELECT DISTINCT execution_id, agent_id FROM sessions;

-- 6. Record migration
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (5, CURRENT_TIMESTAMP);
