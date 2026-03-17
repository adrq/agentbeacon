-- 0017: Remove redundant input column (prompt stored as first session event)
-- SQLite: recreate table without column

CREATE TABLE executions_new (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_execution_id TEXT,
    context_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted','working','input-required','completed','failed','canceled')),
    title TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    max_depth INTEGER NOT NULL DEFAULT 2,
    max_width INTEGER NOT NULL DEFAULT 5,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_execution_id) REFERENCES executions_new(id) ON DELETE CASCADE
);

INSERT INTO executions_new (id, project_id, parent_execution_id, context_id, status,
    title, metadata, max_depth, max_width, created_at, updated_at, completed_at)
SELECT id, project_id, parent_execution_id, context_id, status,
    title, metadata, max_depth, max_width, created_at, updated_at, completed_at
FROM executions;

DROP TABLE executions;

ALTER TABLE executions_new RENAME TO executions;

CREATE INDEX idx_executions_project_id ON executions(project_id);
CREATE INDEX idx_executions_status ON executions(status);
CREATE INDEX idx_executions_context_id ON executions(context_id);
CREATE INDEX idx_executions_created_at ON executions(created_at);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (17, CURRENT_TIMESTAMP);
