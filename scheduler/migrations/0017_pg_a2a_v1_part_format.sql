-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- 0017: Remove redundant input column (prompt stored as first session event)
-- PostgreSQL: simple ALTER TABLE

ALTER TABLE executions DROP COLUMN IF EXISTS input;

INSERT INTO schema_migrations (version, applied_at) VALUES (17, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
