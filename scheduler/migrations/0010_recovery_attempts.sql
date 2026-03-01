ALTER TABLE sessions ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (10, CURRENT_TIMESTAMP);
