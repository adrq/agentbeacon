-- 0022: Add sandbox_policy column to executions and sessions (PostgreSQL).

ALTER TABLE executions ADD COLUMN sandbox_policy TEXT;
ALTER TABLE sessions ADD COLUMN sandbox_policy TEXT;

UPDATE executions SET sandbox_policy = '{"fs_level":"unrestricted"}' WHERE sandbox_policy IS NULL;
UPDATE sessions SET sandbox_policy = '{"fs_level":"unrestricted"}' WHERE sandbox_policy IS NULL;

ALTER TABLE executions ALTER COLUMN sandbox_policy SET NOT NULL;
ALTER TABLE sessions ALTER COLUMN sandbox_policy SET NOT NULL;

INSERT INTO schema_migrations (version, applied_at) VALUES (22, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
