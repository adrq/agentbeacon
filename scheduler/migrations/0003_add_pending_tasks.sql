-- Week 5: Task Queue Persistence for Crash Recovery
-- Adds pending_tasks table to support database-backed task queue
-- Source: specs/015-week-5-current/data-model.md Entity 4

-- Task queue persistence table (NFR-004: crash recovery support)
CREATE TABLE IF NOT EXISTS pending_tasks (
    execution_id TEXT NOT NULL,  -- UUID of workflow execution
    node_id TEXT NOT NULL,  -- Task ID from workflow YAML
    queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Timestamp for FIFO ordering
    task_assignment TEXT NOT NULL,  -- JSON: serialized TaskAssignment object
    PRIMARY KEY (execution_id, node_id),  -- Composite key prevents duplicate task queuing
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE
);

-- Index for FIFO ordering (FR-038: tasks assigned in submission order)
CREATE INDEX IF NOT EXISTS idx_pending_tasks_queued_at
    ON pending_tasks(queued_at);

-- Insert schema version
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (3, CURRENT_TIMESTAMP);
