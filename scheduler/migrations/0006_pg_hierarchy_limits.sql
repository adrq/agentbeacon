-- Migration 0006: Hierarchy limits (PostgreSQL)

-- Add hierarchy limit columns to executions
ALTER TABLE executions ADD COLUMN max_depth INTEGER NOT NULL DEFAULT 2;
ALTER TABLE executions ADD COLUMN max_width INTEGER NOT NULL DEFAULT 5;

-- Seed system-wide defaults in config table
INSERT INTO config (name, value, created_at, updated_at)
VALUES ('max_depth', '2', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT DO NOTHING;
INSERT INTO config (name, value, created_at, updated_at)
VALUES ('max_width', '5', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (version, applied_at)
VALUES (6, CURRENT_TIMESTAMP)
ON CONFLICT DO NOTHING;
