-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Add the network ceiling and its provenance to executions.

ALTER TABLE executions ADD COLUMN ceiling_mode TEXT NOT NULL DEFAULT 'unsandboxed'
    CHECK (ceiling_mode IN ('unsandboxed', 'none', 'allowlist', 'egress'));
ALTER TABLE executions ADD COLUMN ceiling_entries TEXT NOT NULL DEFAULT '[]';
ALTER TABLE executions ADD COLUMN ceiling_by TEXT;
ALTER TABLE executions ADD COLUMN ceiling_at TIMESTAMP;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (30, CURRENT_TIMESTAMP);
