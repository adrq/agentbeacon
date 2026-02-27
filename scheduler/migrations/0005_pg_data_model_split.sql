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

-- 2. Populate drivers from existing agent_type values
INSERT INTO drivers (id, name, platform, config)
SELECT gen_random_uuid()::text, agent_type, agent_type, '{}'
FROM (SELECT DISTINCT agent_type FROM agents) sub;

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
