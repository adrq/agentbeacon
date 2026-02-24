# A2A Server Endpoints Contract

## Overview
Defines the A2A Protocol v0.3.0 server endpoints exposed by the scheduler for external workflow submission and status queries.

---

## Endpoint 1: Agent Card

### Specification
```
GET /.well-known/agent-card.json
Host: localhost:9456
Accept: application/json
```

### Success Response (A2A v0.3.0 Compliant)
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "name": "AgentBeacon Scheduler",
  "version": "1.0.0",
  "protocolVersion": "0.3.0",
  "url": "http://localhost:9456",
  "description": "AgentBeacon scheduler for AI agent workflow orchestration with DAG-based task scheduling and workflow registry support",
  "preferredTransport": "JSONRPC",
  "defaultInputModes": ["application/x-yaml", "text/plain"],
  "defaultOutputModes": ["application/json"],
  "capabilities": {
    "streaming": false,
    "pushNotifications": false,
    "methods": ["message/send", "tasks/get"],
    "features": [
      "workflow-orchestration",
      "dag-scheduling",
      "workflow-registry",
      "namespace-support",
      "fifo-task-assignment"
    ]
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

### Requirements
- Must expose agent card endpoint
- Must validate against `docs/a2a-v0.3.0.schema.json#/definitions/AgentCard`
- Must include all A2A v0.3.0 required fields
- Must declare supported JSON-RPC methods

### A2A v0.3.0 Required Fields
All of these fields **MUST** be present per A2A specification:
- ✅ `name` - Agent identity
- ✅ `version` - Scheduler version
- ✅ `protocolVersion` - A2A protocol version ("0.3.0")
- ✅ `url` - Main endpoint URL
- ✅ `description` - Agent description
- ✅ `preferredTransport` - Transport protocol ("JSONRPC")
- ✅ `defaultInputModes` - Accepted MIME types
- ✅ `defaultOutputModes` - Response MIME types
- ✅ `capabilities` - Supported features and methods
- ✅ `skills` - Agent skill declarations

### Test Scenarios
1. ✅ GET request returns 200 OK
2. ✅ Response content-type is application/json
3. ✅ Response validates against A2A v0.3.0 AgentCard schema
4. ✅ All required fields present
5. ✅ capabilities.methods includes ["message/send", "tasks/get"]
6. ✅ skills array contains at least one skill
7. ✅ preferredTransport matches transport available at url

---

## Endpoint 2: JSON-RPC

### Specification
```
POST /rpc
Host: localhost:9456
Content-Type: application/json
Accept: application/json
```

### JSON-RPC 2.0 Format
All requests/responses follow JSON-RPC 2.0 specification:
```json
{
  "jsonrpc": "2.0",
  "method": "string",
  "params": {},
  "id": "string|number|null"
}
```

### Error Response Format
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32600,
    "message": "Invalid Request",
    "data": "additional error details"
  },
  "id": null
}
```

### Standard Error Codes
- `-32700`: Parse error (invalid JSON)
- `-32600`: Invalid Request (malformed JSON-RPC)
- `-32601`: Method not found
- `-32602`: Invalid params
- `-32603`: Internal error

### Requirements
- Must expose JSON-RPC 2.0 endpoint
- Must validate JSON-RPC 2.0 format
- Must return proper error codes
- Must include request ID in response

### Test Scenarios
1. ✅ POST request with valid JSON-RPC accepted
2. ✅ Invalid JSON returns parse error (-32700)
3. ✅ Missing "jsonrpc": "2.0" returns invalid request (-32600)
4. ✅ Unknown method returns method not found (-32601)
5. ✅ Response includes matching request ID

---

## Method 1: message/send

### Purpose
Non-blocking workflow submission with immediate execution ID return.

### Request (A2A v0.3.0 Compliant)
```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "data",
          "data": {
            "workflowYaml": "name: Example\ntasks:\n  - id: task-1..."
          }
        }
      ],
      "messageId": "msg-uuid-123",
      "kind": "message"
    }
  },
  "id": "req-1"
}
```

### Parameters

Per A2A v0.3.0 spec, `params` MUST contain a `MessageSendParams` object:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `params.message` | Message | Yes | A2A Message object (required by spec) |
| `params.message.role` | String | Yes | Must be "user" for client requests |
| `params.message.parts` | Array[Part] | Yes | Array of message parts |
| `params.message.messageId` | String | Yes | Unique message identifier (UUID) |
| `params.message.kind` | String | Yes | Must be "message" |
| `params.message.contextId` | String | No | Optional context grouping identifier |
| `params.configuration` | Object | No | Optional MessageSendConfiguration |
| `params.metadata` | Object | No | Optional extension metadata |

### Workflow Submission Formats

AgentBeacon supports two formats for workflow submission in `message.parts[0].data`:

**Format A - Direct (RECOMMENDED):**
```json
{
  "kind": "data",
  "data": {
    "workflowYaml": "name: Example\ntasks: [...]",
    "workflowRef": "team/auth:v1.2.3"
  }
}
```

**Format B - Nested (LEGACY - supported for backward compatibility):**
```json
{
  "kind": "data",
  "data": {
    "data": {
      "workflowYaml": "name: Example\ntasks: [...]",
      "workflowRef": "team/auth:v1.2.3"
    }
  }
}
```

Both formats are supported. **Use Format A (direct) for new code.**

**Workflow Selection:**
- Exactly one of `workflowYaml` or `workflowRef` must be provided (XOR constraint)
- `workflowYaml`: Inline workflow YAML string
- `workflowRef`: Registry reference (format: "namespace/name:version" or "namespace/name" defaults to ":latest")

### Success Response
```json
{
  "jsonrpc": "2.0",
  "result": {
    "executionId": "exec-abc123",
    "status": "pending",
    "message": "Workflow submitted successfully"
  },
  "id": "req-1"
}
```

### A2A Behavioral Compliance

**Non-Blocking Only**:
- AgentBeacon scheduler **only supports non-blocking workflow submission**
- Response is always a `Task` object (never a `Message` object)
- The A2A spec allows `message/send` to return either `Task` OR `Message`
- AgentBeacon always returns `Task` with `executionId` for later polling

**Blocking Mode Not Supported**:
- The `MessageSendConfiguration.blocking` parameter is **ignored** if provided
- Rationale: Workflow execution can range from seconds to hours
- Clients must use `tasks/get` to poll for completion status

**A2A Compliance**:
- ✅ This behavior is **compliant** with A2A v0.3.0 specification
- A2A spec (§7.1): "This method is suitable for synchronous request/response interactions **or** when client-side polling (using `tasks/get`) is acceptable for monitoring longer-running tasks"
- Scheduler chooses polling pattern as the practical approach for workflow orchestration

### Error Responses

**Invalid Workflow YAML:**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid workflow YAML",
    "data": "yaml: line 5: mapping values are not allowed in this context"
  },
  "id": "req-1"
}
```

**Schema Validation Failure:**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Workflow schema validation failed",
    "data": "Missing required field: tasks"
  },
  "id": "req-1"
}
```

**Missing workflowRef:**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Workflow reference not found in registry",
    "data": "No workflow found for reference: team/auth:v99.99.99"
  },
  "id": "req-1"
}
```

**Ambiguous Version:**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Ambiguous workflow reference",
    "data": "Multiple versions found for 'team/auth'. Please specify version explicitly (e.g., 'team/auth:v1.2.3' or 'team/auth:latest')"
  },
  "id": "req-1"
}
```

**Circular Dependency:**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid workflow structure",
    "data": "Circular dependency detected in workflow DAG"
  },
  "id": "req-1"
}
```

**Empty DAG:**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid workflow structure",
    "data": "Workflow has no executable nodes (empty DAG)"
  },
  "id": "req-1"
}
```

### Requirements
- Must support message/send method
- Must accept both workflowYaml and workflowRef
- Must validate workflow against schema
- Must detect circular dependencies
- Must reject missing workflowRef with descriptive error
- Must reject empty DAG workflows
- Must reject ambiguous version references

### Test Scenarios
1. ✅ Inline workflow YAML submission returns execution ID
2. ✅ WorkflowRef submission resolves from registry
3. ✅ Invalid YAML returns error with line number
4. ✅ Schema validation failure returns specific field error
5. ✅ Circular dependency detected and rejected
6. ✅ Empty DAG rejected with descriptive error
7. ✅ Missing workflowRef returns "not found" error
8. ✅ Ambiguous version (no version, multiple exist) returns error
9. ✅ contextId passed through to task assignments
10. ✅ Non-blocking: Returns immediately without waiting for completion

---

## Method 2: tasks/get

### Purpose
Query execution status by execution ID.

### Request
```json
{
  "jsonrpc": "2.0",
  "method": "tasks/get",
  "params": {
    "executionId": "exec-abc123"
  },
  "id": "req-2"
}
```

### Success Response (Running)
```json
{
  "jsonrpc": "2.0",
  "result": {
    "executionId": "exec-abc123",
    "status": "running",
    "workflowRef": "team/auth:v1.2.3",
    "createdAt": "2025-10-05T10:00:00Z",
    "updatedAt": "2025-10-05T10:01:30Z",
    "completedAt": null,
    "taskStates": {
      "task-a": {
        "status": "completed",
        "startedAt": "2025-10-05T10:00:05Z",
        "completedAt": "2025-10-05T10:00:30Z"
      },
      "task-b": {
        "status": "running",
        "startedAt": "2025-10-05T10:00:35Z",
        "completedAt": null
      },
      "task-c": {
        "status": "pending",
        "startedAt": null,
        "completedAt": null
      }
    }
  },
  "id": "req-2"
}
```

### Success Response (Completed)
```json
{
  "jsonrpc": "2.0",
  "result": {
    "executionId": "exec-abc123",
    "status": "completed",
    "workflowRef": "team/auth:v1.2.3",
    "createdAt": "2025-10-05T10:00:00Z",
    "updatedAt": "2025-10-05T10:05:00Z",
    "completedAt": "2025-10-05T10:05:00Z",
    "taskStates": {
      "task-a": {"status": "completed", "...": "..."},
      "task-b": {"status": "completed", "...": "..."},
      "task-c": {"status": "completed", "...": "..."}
    }
  },
  "id": "req-2"
}
```

### Error Response (Not Found)
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Execution not found",
    "data": "No execution found with ID: exec-nonexistent"
  },
  "id": "req-2"
}
```

### Requirements
- Must support tasks/get method
- Must return both workflow-level and node-level status
- Must return JSON-RPC error for nonexistent execution
- Must include timestamps (created, updated, completed)
- Must include per-task status breakdown

### Test Scenarios
1. ✅ Valid execution ID returns execution details
2. ✅ Response includes workflow-level status
3. ✅ Response includes per-task status breakdown
4. ✅ Timestamps present (createdAt, updatedAt, completedAt when done)
5. ✅ Nonexistent execution ID returns error (-32602)
6. ✅ Pending execution shows null completedAt
7. ✅ Completed execution shows all tasks complete

---

## Contract Test Requirements

### Positive Test Scenarios

**Test Scenario: Agent Card Accessibility**
- **Given**: Scheduler is running on localhost:9456
- **When**: HTTP GET request to `/.well-known/agent-card.json`
- **Then**:
  - Response status code is 200
  - Content-Type header is `application/json`
  - Response body validates against A2A v0.3.0 AgentCard schema

**Test Scenario: Agent Card Structure**
- **Given**: Agent card is fetched successfully
- **When**: Parsing the JSON response
- **Then**:
  - Field `name` is present
  - Field `protocolVersion` equals "0.3.0"
  - `capabilities.methods` array includes "message/send"
  - `capabilities.methods` array includes "tasks/get"

**Test Scenario: Workflow Submission with Inline YAML**
- **Given**: Valid workflow YAML definition
- **When**: JSON-RPC call to `message/send` with A2A Message containing DataPart with `workflowYaml` field
  ```json
  {
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "data", "data": {"workflowYaml": "yaml string"}}],
        "messageId": "uuid",
        "kind": "message"
      }
    }
  }
  ```
- **Then**:
  - Response contains `result.executionId` (UUID format)
  - Response contains `result.status` equal to "pending"
  - Execution is created in database

**Test Scenario: Workflow Submission by Registry Reference**
- **Given**: Workflow "team/test:v1.0.0" is registered in workflow_version table
- **When**: JSON-RPC call to `message/send` with A2A Message containing DataPart with `workflowRef` field set to "team/test:v1.0.0"
  ```json
  {
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "data", "data": {"workflowRef": "team/test:v1.0.0"}}],
        "messageId": "uuid",
        "kind": "message"
      }
    }
  }
  ```
- **Then**:
  - Response contains `result.executionId`
  - Workflow YAML is resolved from registry
  - Execution references correct workflow version

### Negative Test Scenarios

**Test Scenario: Invalid YAML Rejection**
- **Given**: Malformed YAML string (syntax error)
- **When**: JSON-RPC call to `message/send` with invalid `workflowYaml`
- **Then**:
  - Response contains `error` object
  - Error code is -32602 (Invalid params)
  - Error message describes YAML syntax issue with line number

**Test Scenario: Circular Dependency Detection**
- **Given**: Workflow YAML where task A depends_on task B and task B depends_on task A
- **When**: JSON-RPC call to `message/send` with circular workflow
- **Then**:
  - Response contains `error` object
  - Error message includes "circular" or "cycle"
  - Workflow is NOT created in database

**Test Scenario: Missing Workflow Reference Error**
- **Given**: WorkflowRef "nonexistent/workflow:v1.0.0" does NOT exist in registry
- **When**: JSON-RPC call to `message/send` with missing `workflowRef`
- **Then**:
  - Response contains `error` object
  - Error code is -32602
  - Error message includes "not found" and the specific reference attempted

**Test Scenario: Ambiguous Version Error**
- **Given**:
  - Workflow "team/test:v1.0.0" exists in registry
  - Workflow "team/test:v2.0.0" exists in registry
  - Neither has `is_latest=true` flag set
- **When**: JSON-RPC call to `message/send` with `workflowRef` set to "team/test" (no version)
- **Then**:
  - Response contains `error` object
  - Error message includes "ambiguous"
  - Error message suggests specifying explicit version

---

## Integration with Existing System

### Database Queries
```sql
-- Resolve workflow reference
SELECT yaml_snapshot
FROM workflow_version
WHERE namespace = ? AND name = ? AND version = ?;

-- Get execution status
SELECT id, status, task_states, created_at, updated_at, completed_at
FROM executions
WHERE id = ?;
```

### Scheduler Flow
```
JSON-RPC Request
    ↓
Validate JSON-RPC format
    ↓
Route to method handler (message/send or tasks/get)
    ↓
[message/send path]
    ↓
Resolve workflowRef OR parse workflowYaml
    ↓
Validate against workflow-schema.json
    ↓
Build WorkflowDAG and detect cycles
    ↓
Create Execution record in database
    ↓
Queue entry nodes to TaskQueue
    ↓
Return execution ID immediately

[tasks/get path]
    ↓
Query Execution by ID
    ↓
Return execution state + task breakdown
```

---

## Security Considerations

**Current MVP:**
- No authentication (localhost-only assumption)
- No rate limiting
- No input sanitization beyond schema validation

**Future (Post-MVP):**
- OAuth2/API key authentication
- Rate limiting per client
- Request size limits
- CORS configuration for web clients

---

**Next**: Additional contract specs (worker sync, registry API)
