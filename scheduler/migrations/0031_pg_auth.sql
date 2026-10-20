-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Users and the credentials that authenticate API callers.

CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX idx_users_single ON users ((1));

CREATE TABLE credentials (
    token_hash TEXT PRIMARY KEY NOT NULL,
    principal_type TEXT NOT NULL
        CHECK (principal_type IN ('user', 'session', 'worker')),
    principal_id TEXT NOT NULL,
    label TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX idx_credentials_session ON credentials (principal_type, principal_id) WHERE principal_type = 'session';

INSERT INTO schema_migrations (version, applied_at) VALUES (31, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
