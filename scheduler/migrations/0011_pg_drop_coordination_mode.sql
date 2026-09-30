-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Drop coordination_mode column — MCP-poll integration model removed per D18.
ALTER TABLE sessions DROP COLUMN coordination_mode;

INSERT INTO schema_migrations (version, applied_at)
VALUES (11, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
