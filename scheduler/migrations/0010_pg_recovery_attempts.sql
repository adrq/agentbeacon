-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

ALTER TABLE sessions ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migrations (version, applied_at) VALUES (10, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
