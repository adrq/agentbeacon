# AgentMaestro Workflow Schema (A2A Aligned)

AgentMaestro workflows are described in YAML but validated as JSON documents. This guide explains the schema, the A2A
mapping behind every task, and the path for migrating away from legacy `prompt` fields. It is written for workflow
authors, reviewers, and partner teams that need deterministic validation without reverse-engineering repository code.

## Audience & Goals

| Role | What they gain |
|------|----------------|
| Workflow authors | Understand which fields are required, how to express sequential versus parallel execution, and how to reuse task payloads. |
| QA reviewers | A checklist of validation and dependency constraints to verify before sign-off. |
| Partner integrators | A concise view of how AgentMaestro maps YAML fields onto the A2A Task payload for interoperability. |

## Top-Level Structure

Workflows are YAML documents with the following top-level keys. The authoritative JSON Schema lives at
`docs/workflow-schema.json` and is accompanied by the vendored A2A Draft-07 schema in
`docs/a2a-task.schema.json`.

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| `name` | ✅ | string | — | Human readable identifier, 1–128 characters, used in logs and dashboards. |
| `description` | ❌ | string | — | Optional summary shown in documentation and UI surfaces. |
| `config` | ❌ | object | `{}` | Workflow-wide configuration map passed to every task (e.g., environment bindings). |
| `on_error` | ❌ | enum (`stop_all`, `continue_branches`) | `stop_all` | Controls how execution proceeds when a task fails. |
| `tasks` | ✅ | array | — | Ordered list of task definitions. Parallelism is expressed through `depends_on`. |

### Workflow Identifiers & Registry Integration

The runtime keeps three identifiers in flight for every workflow submission. All of them are validated by the schema and appear in scheduler↔worker sync payloads:

| Field | Location | Source | Purpose |
|-------|----------|--------|---------|
| `name` | Workflow YAML root | Author supplied | Human friendly display label shown in logs, UI, and notifications. Changing it does **not** create a new registry version. |
| `workflowRef` | Scheduler request payload | Caller supplied (optional) | A pointer chosen by the API client to reference a registry entry (e.g., `team/refactor-auth:latest`). Defaults to `name` when omitted for backwards compatibility. |
| `workflowRegistryId` | Scheduler response → worker | Registry resolver | Canonical namespace/name tuple resolved from `workflowRef` or `name`, immutable for a given registry record. |
| `workflowVersion` | Scheduler response → worker | Registry resolver | Monotonic content hash or version string pinned at dispatch time; workers and agents must echo it for audit trails. |

**Lifecycle:** When a workflow definition is submitted, the scheduler first resolves the request-level `workflowRef` (or falls back to the supplied `name`) against the registry. The resolved `workflowRegistryId` and `workflowVersion` are embedded alongside every task payload, including transports such as stdio and ACP. Workers and adapters must treat the pair as opaque strings—never trim, parse, or attempt to mutate them.

**Aliases:** Authors can register aliases (for example, `team/refactor-auth:latest`) that point at a concrete version. The schema allows either the fully qualified ref or a bare `name`; both are normalized by the scheduler before reaching workers.

**Audit trail:** Logs and task records always contain the triad `(workflowRegistryId, workflowVersion, workflowRef)` for traceability. Prefer querying by `workflowRegistryId` + `workflowVersion` when debugging historic runs because `name` may change between releases.

**Do authors set these?** Workflow authors only define `name` in the YAML file. The scheduler injects `workflowRef`, `workflowRegistryId`, and `workflowVersion` into sync responses (and persists them with each execution) based on registry state.

## Task Definition ↔︎ A2A Mapping

Each entry in `tasks` is validated against the `TaskDefinition` schema which delegates the `task` payload to the A2A
Draft-07 schema. The table below summarises the mapping and constraints.

| Workflow field | Required | Type | A2A concept | Notes |
|----------------|----------|------|-------------|-------|
| `id` | ✅ | slug (`^[a-z0-9_-]+$`) | Task identifier | Must be unique across the workflow and referenced by `depends_on`. |
| `agent` | ✅ | string | Agent binding | Points to an agent declared in repository configuration. |
| `task.history[*]` | ✅ | array | A2A `Message` | Each message must include `messageId`, `kind: message`, `role` (`user` or `agent`), and at least one typed `part`. |
| `task.artifacts` | ❌ | array | A2A `Artifact` | Declare expected outputs; each artifact provides an `artifactId` slug and one or more typed `parts` (`kind: text|file|data`). |
| `inputs.artifacts` | ❌ | array | Artifact dependency | Declaratively request upstream artifacts by `artifactId`; scheduler ensures matching `depends_on` edges. |
| `task.contextId` | ❌ | string | A2A `contextId` | Groups related tasks for downstream systems. |
| `task.metadata` | ❌ | object | A2A metadata | Free-form metadata forwarded to the agent transport adapter. |
| `depends_on` | ❌ | array | Dependency edges | Unique list of task IDs that must complete before execution. |
| `execution.timeout` | ❌ | integer ≥ 0 | Timeout override | Overrides the scheduler timeout in seconds. |
| `execution.retry` | ❌ | object | Retry policy | Supports `attempts`, `backoff` (`fixed`, `linear`, `exponential`), and `delay_seconds`. |

### Understanding `contextId`

`contextId` is an **optional grouping hint** that rides along with the task payload and is only interpreted by transports that understand the A2A protocol.

- **When to set it:** Use a short, human-friendly slug when multiple tasks in a workflow represent steps of the same real-world conversation (for example, `onboarding`, `incident-review`). This helps downstream systems collapse those tasks into a single thread. Skip it entirely when you do not care about external grouping—the runtime still correlates tasks by execution ID.
- **Adapter behavior:**
  - **A2A agents** receive the value verbatim in `message/send`. If you omit it, the worker generates a unique value (e.g., `ctx-<timestamp>`) before calling the agent so every remote conversation still has a stable key. Agents that ignore the field simply disregard it.
  - **ACP and stdio agents** do not have a `contextId` concept; the worker drops the field. If the agent needs the information, pass it through `task.metadata` or the protocol-specific request body instead.
- **Scope and safety:** The scheduler never uses `contextId` for dependency resolution or control flow—it is strictly metadata. Feel free to keep using `task.metadata` for richer JSON structures, especially when you need per-run identifiers or adapter-agnostic hints.

## Validation & Integrity Rules

1. **Schema enforcement** – Run `uv run python -m pytest tests/workflows/test_schema_validation.py` after editing a
	 workflow or the schema. The tests load `docs/workflow-schema.json` and the vendored A2A schema to ensure everything
	 validates offline.
2. **Identifier uniqueness** – All `tasks[*].id` values must be unique and referenced consistently in `depends_on`.
3. **Dependency safety** – Cyclic dependencies are rejected by orchestration. Document pending edges clearly and break
	 large graphs into smaller workflows when possible.
4. **Retry bounds** – `retry.attempts` includes the initial run. Use small values (≤3) for idempotent actions and rely
	 on compensation workflows for longer recoveries.
5. **Artifact reuse** – When one task emits an artifact consumed by another, declare the dependency in
	`inputs.artifacts` and reference it in the downstream task message (as shown in the examples below). Artifact IDs must
	be unique across the workflow.
6. **Input integrity** – Every `inputs.artifacts[*]` entry must reference an existing upstream task and one of its
	declared artifacts. The scheduler enforces that a task depends on each upstream producer it references.

### Error Handling Semantics

`on_error` drives orchestration recovery:

- `stop_all` (default) immediately halts the workflow after the first failure and marks subsequent tasks as skipped.
- `continue_branches` only stops the failing branch; independent branches continue execution. Use alongside downstream
	guards that verify artifact availability.

## Annotated Examples

### Sequential Example

File: `examples/workflow_sequential.yaml`

```yaml
name: sequential_onboarding
description: Demonstrates a two-step sequential workflow aligned with the A2A schema mapping.
on_error: stop_all
config:
  environment: sandbox
tasks:
  - id: draft_welcome
    agent: mock-a2a-writer
    task:
      contextId: onboarding
      history:
        - messageId: msg-draft-1
          kind: message
          role: user
          parts:
            - kind: text
              text: |
                Compose a short welcome message introducing AgentMaestro to a new teammate.
      artifacts:
        - artifactId: welcome_note
          parts:
            - kind: text
              text: Draft welcome note placeholder
  - id: summarize_plan
    agent: mock-a2a-editor
    depends_on:
      - draft_welcome
    inputs:
      artifacts:
        - from: draft_welcome
          artifactId: welcome_note
    execution:
      timeout: 120
      retry:
        attempts: 2
        backoff: linear
        delay_seconds: 10
    task:
      contextId: onboarding
      history:
        - messageId: msg-summarize-1
          kind: message
          role: user
          parts:
            - kind: text
              text: |
                Review the draft welcome note artifact and summarize the onboarding plan in two bullet points.
            - kind: text
              text: "Artifact reference: welcome_note"
```

The merge task waits for both upstream branches and records the audience in `metadata` for transport adapters that need
target-specific formatting.

## JSON Schema Snapshot

`docs/workflow-schema.json` contains the authoritative validation logic. The excerpt below highlights the top-level
structure and the `$defs/TaskDefinition` section:

```json
{
	"$schema": "https://json-schema.org/draft/2020-12/schema",
	"$id": "https://schemas.agentmaestro.dev/workflow-schema.json",
	"required": ["name", "tasks"],
	"properties": {
		"name": { "type": "string", "minLength": 1, "maxLength": 128 },
		"tasks": {
			"type": "array",
			"items": { "$ref": "#/$defs/TaskDefinition" }
		}
	},
	"$defs": {
		"TaskDefinition": {
			"required": ["id", "agent", "task"],
			"properties": {
				"task": { "$ref": "https://schemas.agentmaestro.dev/a2a/task-schema.json" }
			}
		}
	}
}
```

Use the full file for validation and automated tooling; keep snippets in documentation succinct to avoid drift.

## Configuration & External References

- **Agent bindings** – `agent` values must correspond to entries in `agents.yaml` (or the relevant environment store).
	Distribute secrets via environment variables or secret managers; never inline credentials in the workflow file.
- **Task metadata** – Store downstream routing hints in `task.metadata`. Because the field is schema-validated as an
	object with arbitrary keys, it is safe to add protocol-specific flags without editing the workflow schema.
- **Artifacts** – Use artifact IDs when referencing generated files, and ensure any external storage locations are
	declared in your agent configuration.

## Legacy `prompt` Migration

Legacy workflows may still use a `prompt` string attached directly to a node. Migrate by:

1. Creating a new `task.history` array with a single `role: user` entry that contains the original prompt text.
2. Moving any structured context into `task.metadata` if required by adapters.
3. Recording outputs with `task.artifacts` so downstream tasks reference `artifact.artifactId` instead of the legacy prompt text.

Once swapped, remove the `prompt` key entirely; the JSON Schema rejects it to prevent regressions.

## Related Resources

- `docs/workflow-schema.json` – full validation schema for workflows.
- `docs/a2a-task.schema.json` – vendored Draft-07 schema for A2A payloads.
- `requirements/a2a-specification.md` – canonical protocol reference for Agent ↔︎ Agent interactions.
- `tests/workflows/test_schema_validation.py` – pytest module exercising the validation flow end-to-end.
