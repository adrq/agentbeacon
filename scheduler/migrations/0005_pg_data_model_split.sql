-- Migration 0005: Data model split (Drivers / Agents / Sessions) — PostgreSQL variant

-- 1. Create drivers table
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

-- 3. Drop agent_type CHECK constraint (platform validation moves to drivers)
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_agent_type_check;

-- 4. Add driver_id column with FK
ALTER TABLE agents ADD COLUMN driver_id TEXT REFERENCES drivers(id);
CREATE INDEX idx_agents_driver_id ON agents(driver_id);

UPDATE agents SET driver_id = d.id
FROM drivers d WHERE agents.agent_type = d.platform;

-- 5. Create execution_agents junction table
CREATE TABLE execution_agents (
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    PRIMARY KEY (execution_id, agent_id),
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);
CREATE INDEX idx_execution_agents_agent_id ON execution_agents(agent_id);

-- 6. Backfill junction from existing sessions
INSERT INTO execution_agents (execution_id, agent_id)
SELECT DISTINCT execution_id, agent_id FROM sessions
ON CONFLICT (execution_id, agent_id) DO NOTHING;

-- 7. Record migration
INSERT INTO schema_migrations (version, applied_at) VALUES (5, CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;
