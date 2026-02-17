-- Migration 0002 (PostgreSQL): Rename workspaces to projects, add new columns
--
-- PostgreSQL supports ALTER TABLE ... RENAME COLUMN directly, so we don't need
-- the recreate-table pattern that SQLite requires.

-- Step 1: Rename workspaces table and columns
ALTER TABLE workspaces RENAME TO projects;
ALTER TABLE projects RENAME COLUMN project_path TO path;
ALTER TABLE projects ADD COLUMN deleted_at TIMESTAMP;

-- Step 2: Rename workspace_id to project_id on executions, add worktree_path
ALTER TABLE executions RENAME COLUMN workspace_id TO project_id;
ALTER TABLE executions ADD COLUMN worktree_path TEXT;

-- Step 3: Rename workspace_id to project_id on artifacts
ALTER TABLE artifacts RENAME COLUMN workspace_id TO project_id;

-- Step 4: Modify agents — remove UNIQUE on name, add deleted_at, add partial unique index
-- Drop the old unique constraint on name (created inline in v1)
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_name_key;
DROP INDEX IF EXISTS agents_name_key;
ALTER TABLE agents ADD COLUMN deleted_at TIMESTAMP;
CREATE UNIQUE INDEX idx_agents_name_active ON agents(name) WHERE deleted_at IS NULL;

-- Step 5: Update indexes on projects (was workspaces)
DROP INDEX IF EXISTS idx_workspaces_project_path;
CREATE INDEX idx_projects_path ON projects(path);

-- Step 6: Rename workspace_id indexes on executions and artifacts
DROP INDEX IF EXISTS idx_executions_workspace_id;
CREATE INDEX idx_executions_project_id ON executions(project_id);
DROP INDEX IF EXISTS idx_artifacts_workspace_id;
CREATE INDEX idx_artifacts_project_id ON artifacts(project_id);

-- Step 7: Add cwd to sessions
ALTER TABLE sessions ADD COLUMN cwd TEXT;

-- Record migration
INSERT INTO schema_migrations (version, applied_at) VALUES (2, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
