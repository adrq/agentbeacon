-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- 0027: Enforce projects.slug NOT NULL (PostgreSQL).
-- Every row was given a slug by the 0026 code step.

ALTER TABLE projects ALTER COLUMN slug SET NOT NULL;

INSERT INTO schema_migrations (version, applied_at) VALUES (27, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
