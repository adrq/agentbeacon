-- Migration 0015: MCP server configuration (PostgreSQL)

CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    transport_type TEXT NOT NULL CHECK (transport_type IN ('stdio', 'http')),
    config TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_mcp_servers (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    mcp_server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE RESTRICT,
    PRIMARY KEY (project_id, mcp_server_id)
);
CREATE INDEX IF NOT EXISTS idx_project_mcp_servers_mcp_server_id ON project_mcp_servers(mcp_server_id);

INSERT INTO schema_migrations (version, applied_at)
VALUES (15, CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;
