-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Index on parent_session_id for recursive CTE performance (get_subtree).
CREATE INDEX IF NOT EXISTS idx_sessions_parent_session_id ON sessions(parent_session_id);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (4, CURRENT_TIMESTAMP);
