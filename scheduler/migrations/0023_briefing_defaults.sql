-- Migration 0023: Update hierarchy defaults and add briefing.use_db_overrides flag

-- Update max_depth default from 2 to 5
INSERT OR REPLACE INTO config (name, value, created_at, updated_at)
VALUES ('max_depth', '5', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- Update max_width default from 5 to 10
INSERT OR REPLACE INTO config (name, value, created_at, updated_at)
VALUES ('max_width', '10', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- Add briefing.use_db_overrides flag (false = use defaults, true = allow overrides)
INSERT OR IGNORE INTO config (name, value, created_at, updated_at)
VALUES ('briefing.use_db_overrides', 'false', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (23, CURRENT_TIMESTAMP);
