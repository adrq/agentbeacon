# AgentMaestro Workflow Schema

Guide to writing AgentMaestro workflows in YAML format, aligned with the A2A v0.3.0 protocol.

---

## Overview

Workflows are YAML documents that define multi-agent task orchestration. Each workflow contains metadata, configuration, and a list of tasks with their dependencies.

---

## Top-Level Structure

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `name` | ✅ | string | — | Human-readable identifier (1–128 characters) |
| `description` | ❌ | string | — | Optional summary shown in logs and UI |
| `config` | ❌ | object | `{}` | Workflow-wide configuration passed to all tasks |
| `on_error` | ❌ | enum | `stop_all` | Error handling: `stop_all` or `continue_branches` |
| `tasks` | ✅ | array | — | List of task definitions (see below) |

---

## Task Definition

Each task in the `tasks` array has these fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | ✅ | string | Unique task identifier (lowercase, numbers, `-`, `_` only) |
| `agent` | ✅ | string | Agent name from your agent configuration |
| `task` | ✅ | object | Message and metadata to send to the agent (A2A MessageSendParams) |
| `depends_on` | ❌ | array | List of task IDs that must complete before this task runs |
| `execution` | ❌ | object | Timeout and retry configuration |

### Task Payload (`task` field)

The `task` field contains the message sent to the agent:

| Field | Required | Description |
|-------|----------|-------------|
| `task.message` | ✅ | The message to send (see Message structure below) |
| `task.configuration` | ❌ | A2A configuration options |
| `task.metadata` | ❌ | Custom metadata for routing or debugging |

### Message Structure (`task.message`)

| Field | Required | Description |
|-------|----------|-------------|
| `role` | ✅ | Message sender: `user` or `agent` |
| `parts` | ✅ | Array of message content parts (text, data, resource) |
| `messageId` | ❌ | Message identifier (auto-generated if omitted) |
| `kind` | ❌ | Message type (defaults to `"message"` if omitted) |

**Note:** `messageId` and `kind` are optional. If you omit them, the scheduler automatically generates appropriate values when dispatching tasks to workers.

### Execution Policy (`execution` field)

| Field | Description |
|-------|-------------|
| `timeout` | Task timeout in seconds (overrides workflow default) |
| `retry.attempts` | Maximum retry attempts (includes initial run) |
| `retry.backoff` | Retry strategy: `fixed`, `linear`, or `exponential` |
| `retry.delay_seconds` | Delay between retries |
| `permission` | Permission policy: `allow`, `deny`, or `ask` (validated but not enforced) |

---

## Dependencies and Parallelism

Tasks run in parallel unless `depends_on` specifies dependencies:

```yaml
tasks:
  - id: task-a
    agent: writer
    task: {...}

  - id: task-b
    agent: reviewer
    task: {...}
    depends_on: [task-a]  # Runs after task-a completes
```

---

## Error Handling

Control workflow behavior when tasks fail using `on_error`:

- **`stop_all`** (default): Stop entire workflow on first failure
- **`continue_branches`**: Only stop the failing branch; independent tasks continue

---

## Example Workflow

```yaml
name: sequential_onboarding
description: Two-step workflow demonstrating task dependencies
on_error: stop_all
config:
  environment: sandbox

tasks:
  - id: draft_welcome
    agent: mock-a2a-writer
    task:
      message:
        # messageId and kind are optional - auto-generated
        role: user
        parts:
          - kind: text
            text: |
              Compose a short welcome message introducing AgentMaestro.
      metadata:
        category: onboarding

  - id: review_welcome
    agent: mock-a2a-editor
    depends_on: [draft_welcome]
    execution:
      timeout: 120
      retry:
        attempts: 2
        backoff: linear
        delay_seconds: 10
    task:
      message:
        role: user
        parts:
          - kind: text
            text: Review the welcome message and suggest improvements.
      metadata:
        category: onboarding
        priority: normal
```

---

## Validation Rules

**Required fields:**
- Every workflow must have `name` and `tasks`
- Every task must have `id`, `agent`, and `task`
- Every message must have `role` and `parts`

**Constraints:**
- Task IDs must be unique within a workflow
- Task IDs can only contain lowercase letters, numbers, hyphens, and underscores
- Dependencies in `depends_on` must reference existing task IDs
- Circular dependencies are rejected

**Testing:**
Run `uv run pytest tests/workflows/test_schema_validation.py` to validate your workflows.

---

## Optional Fields: messageId and kind

Both `messageId` and `kind` in the message are **optional**, making workflows cleaner to write:

**Minimal example:**
```yaml
task:
  message:
    role: user
    parts:
      - kind: text
        text: "Hello, agent!"
```

**With explicit values:**
```yaml
task:
  message:
    messageId: custom-id-123
    kind: message
    role: user
    parts:
      - kind: text
        text: "Hello, agent!"
```

If you provide values, they're preserved exactly. If omitted, the scheduler generates them automatically.

---

## Workflow Registry

Workflows can be stored in the registry with versioning:

**Registry Identifiers:**
- `workflowRef` - Reference like `team/refactor-auth:latest` (provided by caller)
- `workflowRegistryId` - Canonical `namespace/name` (resolved by scheduler)
- `workflowVersion` - Specific version or content hash (immutable)

Workflow authors only need to define `name` in the YAML. The registry handles versioning and references.

---

## Related Resources

- `docs/workflow-schema.json` - JSON Schema for validation
- `docs/a2a-v0.3.0.schema.json` - A2A v0.3.0 protocol specification
