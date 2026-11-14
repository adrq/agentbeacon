-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Project secrets and their attachment to executions.

CREATE TABLE secrets (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE UNIQUE INDEX idx_secrets_name_active ON secrets (project_id, name) WHERE deleted_at IS NULL;

CREATE TABLE execution_secrets (
    id TEXT PRIMARY KEY NOT NULL,
    execution_id TEXT NOT NULL,
    secret_id TEXT NOT NULL,
    attached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detached_at TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id),
    FOREIGN KEY (secret_id) REFERENCES secrets(id)
);

CREATE UNIQUE INDEX idx_execution_secrets_active ON execution_secrets (execution_id, secret_id) WHERE detached_at IS NULL;

CREATE INDEX idx_execution_secrets_secret_active ON execution_secrets (secret_id) WHERE detached_at IS NULL;

INSERT INTO schema_migrations (version, applied_at) VALUES (32, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
