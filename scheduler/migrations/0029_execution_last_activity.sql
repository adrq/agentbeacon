-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Add last_activity_at to executions.

ALTER TABLE executions ADD COLUMN last_activity_at TIMESTAMP NOT NULL DEFAULT '1970-01-01 00:00:00';
UPDATE executions SET last_activity_at = updated_at;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (29, CURRENT_TIMESTAMP);
