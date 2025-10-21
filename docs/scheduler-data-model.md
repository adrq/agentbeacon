# Scheduler Data Model

High-level overview of core data entities and their relationships in the AgentMaestro scheduler.

---

## WorkflowVersion (Registry Entry)

**Purpose**: Stores versioned workflow definitions with namespace organization.

**Key Fields:**
- `namespace`, `name`, `version` - Composite primary key for versioned workflows
- `is_latest` - Marks the latest version for `:latest` resolution
- `content_hash` - SHA-256 hash for integrity verification
- `yaml_snapshot` - Complete workflow YAML content
- `git_repo`, `git_path`, `git_commit`, `git_branch` - Optional Git provenance

**Example:**
```json
{
  "namespace": "team",
  "name": "refactor-auth",
  "version": "v1.2.3",
  "is_latest": true,
  "yaml_snapshot": "name: Refactor Auth\ntasks:\n  - id: analyze..."
}
```

---

## WorkflowDAG (In-Memory)

**Purpose**: Parsed dependency graph for scheduling logic. Built from workflow YAML at execution start.

**Structure:**
- `tasks` - Map of task IDs to task definitions
- `dependencies` - Map of task IDs to their dependencies
- `dependents` - Reverse dependency map for efficient scheduling

**Key Operations:**
- `from_workflow(yaml)` - Parse YAML and build DAG
- `detect_cycles()` - Validate no circular dependencies
- `entry_nodes()` - Get tasks with no dependencies
- `ready_nodes(completed)` - Get tasks ready to execute

---

## TaskAssignment (Queue Entry)

**Purpose**: Task ready for worker execution with all required metadata.

**Key Fields:**
- `execution_id`, `node_id` - Unique task identifier
- `agent` - Agent name to execute the task
- `task` - A2A MessageSendParams payload
- `workflow_registry_id`, `workflow_version`, `workflow_ref` - Registry metadata
- `protocol_metadata` - Scheduler hints for adapters

**Example:**
```json
{
  "execution_id": "exec-123",
  "node_id": "task-analyze",
  "agent": "mock-a2a-writer",
  "task": {
    "message": {
      "messageId": "550e8400-e29b-41d4-a716-446655440000",
      "kind": "message",
      "role": "user",
      "parts": [{"kind": "text", "text": "Analyze code quality"}]
    },
    "metadata": {"priority": "normal"}
  }
}
```

---

## TaskQueue

**Purpose**: FIFO queue for pending tasks with database persistence and in-memory cache.

**Implementation:**
- Database-backed for crash recovery
- In-memory cache for performance
- FIFO ordering by submission time

**Operations:**
- `push(task)` - Add task to queue
- `pop()` - Get next task for worker
- `rebuild_from_db()` - Restore queue after crash

---

## ExecutionState

**Purpose**: Tracks workflow execution progress with per-task status.

**Database Fields:**
- `id` - Execution UUID
- `workflow_id` - Workflow reference
- `status` - Overall status (pending, running, completed, failed, cancelled)
- `task_states` - JSON map of per-task status
- `workflow_namespace`, `workflow_version` - Registry metadata

**In-Memory:**
- Active executions cached with DAG and completion state
- Enables fast ready-node calculation

**Task States Format:**
```json
{
  "task-a": {"status": "completed", "started_at": "...", "completed_at": "..."},
  "task-b": {"status": "running", "started_at": "...", "completed_at": null},
  "task-c": {"status": "pending", "started_at": null, "completed_at": null}
}
```

**Status Values:**
- `pending` - Execution created, waiting for first task
- `running` - At least one task executing
- `completed` - All tasks successful
- `failed` - At least one task failed
- `cancelled` - User-initiated cancellation

---

## AgentCard (A2A Discovery)

**Purpose**: A2A Protocol v0.3.0 compliant agent card describing scheduler capabilities.

**Key Fields:**
- `name`, `version`, `protocolVersion` - Scheduler identity
- `url` - Main endpoint
- `preferredTransport` - "JSONRPC"
- `capabilities` - Supported methods and features
- `skills` - Available workflows and operations

**Endpoint:** `GET /.well-known/agent-card.json`

---

## Entity Relationships

```
WorkflowVersion (DB)
    ↓ referenced by
Execution (DB)
    ↓ builds
WorkflowDAG (Memory) ────→ queues tasks ────→ TaskQueue (Memory + DB)
    ↓ updates                                        ↓ assigns to
ExecutionState (Memory + DB)                      Worker
    ↓ stores                                          ↓ reports
TaskResult → completion events → DAG → ready_nodes() → TaskQueue
```

---

## Data Flow

**Workflow Submission:**
```
A2A message/send → Resolve workflowRef → Load WorkflowVersion
→ Parse YAML → Build WorkflowDAG → Validate cycles
→ Create Execution → Queue entry nodes → TaskQueue
```

**Worker Sync:**
```
Worker polls /api/worker/sync → Pop TaskQueue
→ Return TaskAssignment (or no_action)
```

**Task Completion:**
```
Worker reports result → Update ExecutionState (DB + Memory)
→ DAG.ready_nodes(completed) → Queue newly-ready tasks
→ If all complete: Mark Execution complete
```

**Status Query:**
```
A2A tasks/get → Query Execution (DB) → Return task_states JSON
```
