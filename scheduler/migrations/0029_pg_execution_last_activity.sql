-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Add last_activity_at to executions.

ALTER TABLE executions ADD COLUMN last_activity_at TIMESTAMP;
UPDATE executions SET last_activity_at = updated_at;
ALTER TABLE executions ALTER COLUMN last_activity_at SET NOT NULL;
ALTER TABLE executions ALTER COLUMN last_activity_at SET DEFAULT CURRENT_TIMESTAMP;

INSERT INTO schema_migrations (version, applied_at) VALUES (29, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
