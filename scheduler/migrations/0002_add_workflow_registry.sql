-- Week 5: Workflow Registry with Namespace Support
-- Adds workflow_version table for versioned workflow storage
-- Source: specs/015-week-5-current/data-model.md Entity 1

-- Workflow registry with namespace-based organization
CREATE TABLE IF NOT EXISTS workflow_version (
    namespace TEXT NOT NULL,  -- Namespace identifier (^[a-z0-9_-]+$)
    name TEXT NOT NULL,  -- Workflow name within namespace
    version TEXT NOT NULL,  -- Version identifier (e.g., "v1.2.3", commit hash)
    is_latest BOOLEAN NOT NULL DEFAULT false,  -- Marks latest version for :latest resolution
    content_hash TEXT NOT NULL,  -- SHA-256 hash of yaml_snapshot for integrity
    yaml_snapshot TEXT NOT NULL,  -- Complete workflow YAML content
    git_repo TEXT,  -- Optional: Git repository URL
    git_path TEXT,  -- Optional: Path within repository
    git_commit TEXT,  -- Optional: Git commit hash
    git_branch TEXT,  -- Optional: Git branch name
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, name, version)  -- Composite key ensures unique versioned workflows
);

-- Index for :latest version lookups (FR-023)
CREATE INDEX IF NOT EXISTS idx_workflow_version_latest
    ON workflow_version(namespace, name, is_latest);

-- Index for content deduplication and integrity checks
CREATE INDEX IF NOT EXISTS idx_workflow_version_hash
    ON workflow_version(content_hash);

-- Add registry columns to executions table for workflow provenance tracking
-- These columns link executions to specific registry versions
ALTER TABLE executions ADD COLUMN workflow_namespace TEXT;
ALTER TABLE executions ADD COLUMN workflow_version TEXT;

-- Insert schema version
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (2, CURRENT_TIMESTAMP);
