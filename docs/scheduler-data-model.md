# Data Model: Week 5 Features

**Feature**: A2A Server, Task Queue, DAG Scheduling, Registry
**Date**: 2025-10-05

## Overview
This document defines the core data entities and their relationships for Week 5 scheduler features. All entities persist to database (PostgreSQL/SQLite) with in-memory caching for performance.

---

## Entity 1: WorkflowVersion (Registry Entry)

### Purpose
Stores versioned workflow definitions with namespace organization and optional Git provenance tracking.

### Fields

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `namespace` | TEXT | NOT NULL, PK (part 1/3) | Namespace for organization (e.g., "team", "personal") |
| `name` | TEXT | NOT NULL, PK (part 2/3) | Workflow name within namespace |
| `version` | TEXT | NOT NULL, PK (part 3/3) | Version identifier (e.g., "v1.2.3", commit hash) |
| `is_latest` | BOOLEAN | NOT NULL, DEFAULT false | Marks latest version for `:latest` resolution |
| `content_hash` | TEXT | NOT NULL | SHA-256 hash of yaml_snapshot for integrity |
| `yaml_snapshot` | TEXT | NOT NULL | Complete workflow YAML content |
| `git_repo` | TEXT | NULL | Optional: Git repository URL |
| `git_path` | TEXT | NULL | Optional: Path within repository |
| `git_commit` | TEXT | NULL | Optional: Git commit hash |
| `git_branch` | TEXT | NULL | Optional: Git branch name |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Registration timestamp |

### Primary Key
`(namespace, name, version)` - Composite key ensures unique versioned workflows.

### Indexes
```sql
CREATE INDEX idx_workflow_version_latest
    ON workflow_version(namespace, name, is_latest);

CREATE INDEX idx_workflow_version_hash
    ON workflow_version(content_hash);
```

### Validation Rules
- FR-020: `(namespace, name, version)` must be unique
- FR-021: Multiple versions can exist for same `(namespace, name)`
- FR-022: Namespace format: `^[a-z0-9_-]+$`
- FR-025: `yaml_snapshot` must validate against workflow-schema.json
- FR-040: Only one version can have `is_latest=true` per `(namespace, name)`

### State Transitions
1. **Created**: Manual registration via API
2. **Updated**: Git sync updates `git_commit` (out of scope for Week 5)
3. **Deleted**: Soft delete by marking inactive (out of scope for Week 5)

### Relationships
- **Referenced by**: Execution (via `workflow_namespace` + `workflow_version`)
- **Contains**: Workflow YAML defining tasks and dependencies

### Example
```json
{
  "namespace": "team",
  "name": "refactor-auth",
  "version": "v1.2.3",
  "is_latest": true,
  "content_hash": "sha256:a3f4b2c1...",
  "yaml_snapshot": "name: Refactor Auth\ntasks:\n  - id: analyze...",
  "git_repo": null,
  "git_path": null,
  "git_commit": null,
  "git_branch": null,
  "created_at": "2025-10-05T10:00:00Z"
}
```

---

## Entity 2: WorkflowDAG (In-Memory Only)

### Purpose
Represents parsed workflow dependency graph for scheduling logic. Built from WorkflowVersion YAML at execution start.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `tasks` | HashMap<String, Task> | task_id → task details |
| `dependencies` | HashMap<String, Vec<String>> | task_id → list of dependency task_ids |
| `dependents` | HashMap<String, Vec<String>> | task_id → list of tasks that depend on this task |

### Operations

| Method | Return Type | Description |
|--------|-------------|-------------|
| `from_workflow(yaml)` | Result<WorkflowDAG> | Parse YAML and build DAG structures |
| `detect_cycles()` | Result<()> | DFS-based cycle detection (FR-014) |
| `entry_nodes()` | Vec<String> | Tasks with no dependencies (FR-015) |
| `ready_nodes(completed)` | Vec<String> | Tasks whose dependencies are all complete (FR-016) |

### Validation Rules
- FR-014: Must not contain cycles (enforced by `detect_cycles()`)
- FR-037: Must contain at least one task (non-empty DAG)
- All dependencies must reference valid task IDs

### Example
```rust
WorkflowDAG {
    tasks: {
        "task-a": Task { id: "task-a", agent: "writer", ... },
        "task-b": Task { id: "task-b", agent: "reviewer", ... },
    },
    dependencies: {
        "task-a": vec![],
        "task-b": vec!["task-a"],
    },
    dependents: {
        "task-a": vec!["task-b"],
        "task-b": vec![],
    },
}
```

---

## Entity 3: TaskAssignment (Queue Entry)

### Purpose
Represents a task ready for worker execution, containing all metadata needed for task execution and result correlation.

### Fields

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `execution_id` | String | NOT NULL | UUID correlating task to execution |
| `node_id` | String | NOT NULL | Task ID from workflow YAML |
| `agent` | String | NOT NULL | Agent name from agents.yaml |
| `task` | JSON Object | NOT NULL | A2A task payload (validated against a2a-task.schema.json) |
| `workflow_registry_id` | String | NULL | Optional: `namespace/name` from registry |
| `workflow_version` | String | NULL | Optional: Version identifier |
| `workflow_ref` | String | NULL | Optional: Original reference (e.g., "team/auth:latest") |
| `protocol_metadata` | JSON Object | NULL | Optional: Scheduler hints for adapters |

### Validation Rules
- FR-009: Must include complete TaskAssignment structure
- FR-010: `task` field must validate against a2a-task.schema.json
- FR-029: `task` must include A2A-compliant structure (history, artifacts, contextId, metadata)
- FR-030: Auto-generate `task.contextId` if not provided

### Task Correlation
- Unique identifier: `(execution_id, node_id)` tuple
- Workers report results using this tuple for correlation (FR-012)

### Example
```json
{
  "execution_id": "exec-123",
  "node_id": "task-analyze",
  "agent": "mock-a2a-writer",
  "task": {
    "history": [{
      "messageId": "msg-1",
      "kind": "message",
      "role": "user",
      "parts": [{"kind": "text", "text": "Analyze code quality"}]
    }],
    "artifacts": [],
    "contextId": "session-42",
    "metadata": {"priority": "normal"}
  },
  "workflow_registry_id": "team/refactor-auth",
  "workflow_version": "v1.2.3",
  "workflow_ref": "team/refactor-auth:latest",
  "protocol_metadata": {"preferredTransport": "jsonrpc"}
}
```

---

## Entity 4: TaskQueue (Database + In-Memory Cache)

### Purpose
Database-backed FIFO queue with in-memory cache for fast worker assignment with crash recovery support.

### Database Schema (pending_tasks table)

```sql
CREATE TABLE IF NOT EXISTS pending_tasks (
    execution_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    task_assignment TEXT NOT NULL,  -- JSON: serialized TaskAssignment object
    PRIMARY KEY (execution_id, node_id),
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pending_tasks_queued_at
    ON pending_tasks(queued_at);
```

### In-Memory Cache

| Field | Type | Description |
|-------|------|-------------|
| `queue` | Arc<Mutex<VecDeque<TaskAssignment>>> | Performance cache of pending_tasks table |

### Operations

| Method | Return Type | Description |
|--------|-------------|-------------|
| `push(task)` | async Result<()> | Insert to DB + add to VecDeque cache |
| `pop()` | async Option<TaskAssignment> | Pop from VecDeque + delete from DB |
| `len()` | usize | Current queue depth (cache size) |
| `rebuild_from_db()` | async Result<()> | Crash recovery: rebuild VecDeque from DB |

### Write-Through Cache Pattern

**Queue Operation (Push)**:
1. Insert to `pending_tasks` table (source of truth)
2. Push to VecDeque cache (fast reads)
3. Both operations in transaction for consistency

**Dequeue Operation (Pop)**:
1. Pop from VecDeque (O(1) fast path)
2. Delete from `pending_tasks` table
3. Return task to worker

**Crash Recovery**:
1. Query: `SELECT * FROM pending_tasks ORDER BY queued_at`
2. Rebuild VecDeque from results
3. Resume normal operation

### Concurrency
- Uses tokio::sync::Mutex for async-safe access
- Database transactions prevent corruption (NFR-002)
- Lock held only during push/pop (microseconds)
- Multiple workers can poll concurrently

### FIFO Semantics
- FR-038: Tasks assigned in submission order
- Preserved via `queued_at` timestamp + ORDER BY
- Workflow submission time determines ordering
- Within workflow: Tasks queued as dependencies satisfied

### Validation Rules
- NFR-004: Pending tasks survive scheduler crashes
- FR-011: Track task state transitions (pending → assigned)
- Constitution Principle IV: Database-centric state management

---

## Entity 5: ExecutionState (Database + In-Memory)

### Purpose
Tracks overall workflow execution progress with per-node status.

### Database Schema (Existing - Week 4)
```sql
CREATE TABLE executions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,  -- Week 4: workflow name, Week 5: workflow reference
    status TEXT NOT NULL,       -- pending|running|completed|failed|cancelled
    task_states TEXT NOT NULL,  -- JSON: { "node-id": { "status": "...", ... } }
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    workflow_namespace TEXT,    -- Week 5: Added for registry support
    workflow_version TEXT,      -- Week 5: Added for registry support
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);
```

### In-Memory (Active Executions)
```rust
struct ActiveExecution {
    dag: WorkflowDAG,              // Workflow dependency graph
    completed: HashSet<String>,     // Set of completed task IDs
    status: ExecutionStatus,        // Overall status
}

// Global map
active_executions: Arc<RwLock<HashMap<String, ActiveExecution>>>
```

### Task States JSON Format
```json
{
  "task-a": {
    "status": "completed",
    "started_at": "2025-10-05T10:00:00Z",
    "completed_at": "2025-10-05T10:01:00Z",
    "error": null
  },
  "task-b": {
    "status": "running",
    "started_at": "2025-10-05T10:01:05Z",
    "completed_at": null,
    "error": null
  },
  "task-c": {
    "status": "pending",
    "started_at": null,
    "completed_at": null,
    "error": null
  }
}
```

### State Transitions
1. **Pending**: Execution created, waiting for first task
2. **Running**: At least one task executing
3. **Completed**: All tasks successful
4. **Failed**: At least one task failed (stop-all behavior per clarification)
5. **Cancelled**: User-initiated cancellation

### Validation Rules
- FR-011: Track state (pending, assigned, running, completed, failed) per task
- FR-019: Track progress at workflow level AND node level
- NFR-004: Persist state to survive restarts

---

## Entity 6: AgentCard (JSON Response)

### Purpose
A2A Protocol v0.3.0 compliant agent card describing scheduler capabilities.

### Fields (A2A v0.3.0 Required Fields)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | String | ✅ | "AgentMaestro Scheduler" |
| `version` | String | ✅ | Scheduler version (e.g., "1.0.0") |
| `protocolVersion` | String | ✅ | "0.3.0" (A2A protocol version) |
| `url` | String | ✅ | "http://localhost:9456" (main endpoint) |
| `description` | String | ✅ | Scheduler description |
| `defaultInputModes` | String[] | ✅ | ["application/x-yaml", "text/plain"] |
| `defaultOutputModes` | String[] | ✅ | ["application/json"] |
| `capabilities` | Object | ✅ | Supported methods and features |
| `skills` | Skill[] | ✅ | Array of agent skills |
| `preferredTransport` | String | ✅ | "JSONRPC" |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `additionalInterfaces` | AgentInterface[] | Additional transport endpoints |
| `documentationUrl` | String | Link to scheduler documentation |
| `iconUrl` | String | Link to scheduler icon |

### Endpoint
`GET /.well-known/agent-card.json` (FR-001)

### Validation
- **Schema**: Must validate against `docs/a2a-v0.3.0.schema.json#/definitions/AgentCard`
- **A2A Compliance**: All required fields per A2A v0.3.0 specification
- **Transport Declaration**: `preferredTransport` must match transport available at `url`

### Example (A2A v0.3.0 Compliant)
```json
{
  "name": "AgentMaestro Scheduler",
  "version": "1.0.0",
  "protocolVersion": "0.3.0",
  "url": "http://localhost:9456",
  "description": "AgentMaestro scheduler for AI agent workflow orchestration with DAG-based task scheduling and workflow registry support",
  "preferredTransport": "JSONRPC",
  "defaultInputModes": ["application/x-yaml", "text/plain"],
  "defaultOutputModes": ["application/json"],
  "capabilities": {
    "streaming": false,
    "pushNotifications": false,
    "methods": ["message/send", "tasks/get"],
    "features": ["workflow-orchestration", "dag-scheduling", "workflow-registry"]
  },
  "skills": [
    {
      "id": "workflow-orchestration",
      "name": "Workflow Orchestration",
      "description": "Submit and execute multi-agent AI workflows via DAG scheduling. Supports both inline YAML and registry-based workflow references with versioning and namespace organization.",
      "inputModes": ["application/x-yaml", "text/plain"],
      "outputModes": ["application/json"]
    }
  ],
  "additionalInterfaces": [
    {
      "url": "http://localhost:9456",
      "transport": "JSONRPC"
    }
  ]
}
```

### A2A Behavioral Notes
- **Non-Blocking Only**: Scheduler only supports non-blocking `message/send` (always returns `Task`, never `Message`)
- **Blocking Parameter**: The `configuration.blocking` parameter in `MessageSendParams` is **not supported** (will be ignored)
- **Rationale**: Workflows can run from seconds to hours; non-blocking submission + polling via `tasks/get` is the only practical pattern
- **Compliance**: This is compliant with A2A v0.3.0 as the spec allows servers to choose their response mode

---

## Entity Relationships

```
WorkflowVersion (DB)
    ↓ referenced by
Execution (DB)
    ↓ builds
WorkflowDAG (Memory) ────→ queues tasks ────→ TaskQueue (Memory)
    ↓ updates                                        ↓ assigns to
ExecutionState (Memory + DB)                      Worker
    ↓ stores                                          ↓ reports
TaskResult → completion events → DAG → ready_nodes() → TaskQueue
```

### Data Flow

1. **Workflow Submission**:
   ```
   A2A message/send → Resolve workflowRef → Load WorkflowVersion
   → Parse YAML → Build WorkflowDAG → Validate cycles
   → Create Execution → Queue entry nodes → TaskQueue
   ```

2. **Worker Sync**:
   ```
   Worker polls /api/worker/sync → Pop TaskQueue
   → Return TaskAssignment (or no_action)
   ```

3. **Task Completion**:
   ```
   Worker reports result → Update ExecutionState (DB + Memory)
   → DAG.ready_nodes(completed) → Queue newly-ready tasks
   → If all complete: Mark Execution complete
   ```

4. **Status Query**:
   ```
   A2A tasks/get → Query Execution (DB) → Return task_states JSON
   ```

---

## Database Migrations

### Migration 0002: Workflow Registry
**File**: `scheduler/migrations/0002_add_workflow_registry.sql`

```sql
-- Add workflow_version table for registry support
CREATE TABLE IF NOT EXISTS workflow_version (
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    is_latest BOOLEAN NOT NULL DEFAULT false,
    content_hash TEXT NOT NULL,
    yaml_snapshot TEXT NOT NULL,
    git_repo TEXT,
    git_path TEXT,
    git_commit TEXT,
    git_branch TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, name, version)
);

CREATE INDEX IF NOT EXISTS idx_workflow_version_latest
    ON workflow_version(namespace, name, is_latest);

CREATE INDEX IF NOT EXISTS idx_workflow_version_hash
    ON workflow_version(content_hash);

-- Add registry columns to executions (optional foreign keys for future)
ALTER TABLE executions ADD COLUMN workflow_namespace TEXT;
ALTER TABLE executions ADD COLUMN workflow_version TEXT;
```

### Migration 0003: Task Queue Persistence
**File**: `scheduler/migrations/0003_add_pending_tasks.sql`

```sql
-- Add pending_tasks table for crash recovery support (NFR-004)
CREATE TABLE IF NOT EXISTS pending_tasks (
    execution_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    task_assignment TEXT NOT NULL,  -- JSON: serialized TaskAssignment object
    PRIMARY KEY (execution_id, node_id),
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pending_tasks_queued_at
    ON pending_tasks(queued_at);
```

---

## Validation Summary

| Entity | Schema | Validation Point |
|--------|--------|------------------|
| WorkflowVersion | workflow-schema.json | At registration |
| TaskAssignment.task | a2a-task.schema.json | Before queueing |
| WorkflowDAG | Cycle detection | After YAML parse |
| ExecutionState | JSON schema (informal) | At state update |

---

## Performance Considerations

**In-Memory Structures:**
- TaskQueue: O(1) push/pop, grows with pending task count
- WorkflowDAG: O(V+E) for ready_nodes, O(V+E) for cycle detection
- ActiveExecutions: O(1) lookup by execution_id

**Database:**
- WorkflowVersion lookup: Indexed by (namespace, name, version) - O(log N)
- ExecutionState update: Primary key lookup - O(1)
- Execution history queries: Indexed by created_at - O(log N)

**Scalability:**
- Typical: 10 concurrent workflows × 20 nodes = 200 tasks in queue
- Memory footprint: ~50KB per workflow (DAG + state)
- Database: Grows linearly with execution history (indefinite retention per NFR-007)

---

**Next**: Contract definitions (API endpoints, JSON schemas, test scenarios)
