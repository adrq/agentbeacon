-- Drop coordination_mode column — MCP-poll integration model removed per D18.
-- All sessions are SDK-direct; this column has been 'sdk' for every row since inception.
ALTER TABLE sessions DROP COLUMN coordination_mode;

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (11, CURRENT_TIMESTAMP);
