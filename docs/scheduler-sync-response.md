# Scheduler Sync Response Contract

## Overview
The scheduler returns this payload to workers so they can construct protocol-specific envelopes without redefining task content. All identifiers are aligned with the workflow registry, and the `task` body defers to the vendored A2A v1.0 schema.

## Required Fields
- `workflowRegistryId` – Namespace-qualified name (e.g., `team/refactor-auth`).
- `workflowVersion` – Immutable version key (Git commit hash or UUID).
- `workflowRef` – Original reference provided by the caller; logged for traceability.
- `task` – Canonical object validating against `docs/a2a-v1.0-flat.schema.json#/definitions/Send Message Request`.
- `protocolMetadata` (optional) – Scheduler hints for downstream adapters (e.g., preferred transport).

## Sample Payload
```json
{
  "workflowRegistryId": "team/refactor-auth",
  "workflowVersion": "a3f4b2c1",
  "workflowRef": "team/refactor-auth:latest",
  "agent": "mock-a2a-writer",
  "task": {
    "message": {
      "messageId": "550e8400-e29b-41d4-a716-446655440000",
      "role": "ROLE_USER",
      "parts": [
        {
          "text": "Compose a welcome message introducing AgentBeacon to a new teammate."
        }
      ]
    },
    "metadata": { "priority": "normal" }
  },
  "protocolMetadata": {
    "preferredTransport": "jsonrpc"
  }
}
```

> **Note:** Schema validation for `task` is performed by referencing `docs/a2a-v1.0-flat.schema.json#/definitions/Send Message Request` directly.
