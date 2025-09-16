# Workflow Registry API Contract

**Feature**: Week 5 - Workflow Registry
**Date**: 2025-10-05

## Overview
Defines REST API endpoints for workflow registry management with namespace support, versioning, and manual registration.

---

## Endpoint 1: Register Workflow

### Specification
```
POST /api/registry/workflows
Host: localhost:9456
Content-Type: application/json
Accept: application/json
```

### Request Body
```json
{
  "namespace": "team",
  "name": "refactor-auth",
  "version": "v1.2.3",
  "isLatest": true,
  "workflowYaml": "name: Refactor Auth\ntasks:\n  - id: analyze..."
}
```

### Success Response
```http
HTTP/1.1 201 Created
Content-Type: application/json

{
  "workflowRegistryId": "team/refactor-auth",
  "version": "v1.2.3",
  "contentHash": "sha256:a3f4b2c1d5e6f7...",
  "createdAt": "2025-10-05T10:00:00Z",
  "message": "Workflow registered successfully"
}
```

### Error Responses

**Duplicate Version:**
```http
HTTP/1.1 409 Conflict

{
  "error": "Workflow version already exists",
  "details": "Workflow 'team/refactor-auth:v1.2.3' is already registered. Use a different version or update existing."
}
```

**Invalid YAML:**
```http
HTTP/1.1 400 Bad Request

{
  "error": "Invalid workflow YAML",
  "details": "yaml: line 3: mapping values are not allowed in this context"
}
```

**Schema Validation Failure:**
```http
HTTP/1.1 400 Bad Request

{
  "error": "Workflow schema validation failed",
  "details": "Missing required field: tasks"
}
```

**Invalid Namespace:**
```http
HTTP/1.1 400 Bad Request

{
  "error": "Invalid namespace format",
  "details": "Namespace must match pattern ^[a-z0-9_-]+$"
}
```

### Requirements
- FR-020: Workflow registry with unique identifiers
- FR-021: Support multiple versions for same workflow
- FR-022: Namespace-based organization
- FR-024: Manual workflow registration
- FR-025: Store required metadata (namespace, name, version, yaml, hash)

---

## Endpoint 2: Get Workflow by Reference

### Specification
```
GET /api/registry/workflows/{namespace}/{name}/{version}
Host: localhost:9456
Accept: application/json
```

### Examples
```
GET /api/registry/workflows/team/refactor-auth/v1.2.3
GET /api/registry/workflows/team/refactor-auth/latest
```

### Success Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "namespace": "team",
  "name": "refactor-auth",
  "version": "v1.2.3",
  "isLatest": true,
  "workflowYaml": "name: Refactor Auth\ntasks: ...",
  "contentHash": "sha256:a3f4b2c1...",
  "gitRepo": null,
  "gitCommit": null,
  "createdAt": "2025-10-05T10:00:00Z"
}
```

### Error Responses

**Not Found:**
```http
HTTP/1.1 404 Not Found

{
  "error": "Workflow not found",
  "details": "No workflow found with reference: team/refactor-auth:v99.99.99"
}
```

**Ambiguous Reference (Multiple Versions, No `latest`):**
```http
HTTP/1.1 400 Bad Request

{
  "error": "Ambiguous workflow reference",
  "details": "Multiple versions exist for 'team/refactor-auth' but none marked as latest. Specify version explicitly."
}
```

### Requirements
- FR-023: Workflow lookup by reference
- FR-035: Reject missing workflowRef with descriptive error
- FR-040: Reject ambiguous version references

---

## Endpoint 3: List Workflow Versions

### Specification
```
GET /api/registry/workflows/{namespace}/{name}
Host: localhost:9456
Accept: application/json
```

### Success Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "namespace": "team",
  "name": "refactor-auth",
  "versions": [
    {
      "version": "v1.2.3",
      "isLatest": true,
      "createdAt": "2025-10-05T10:00:00Z",
      "contentHash": "sha256:a3f4b2c1..."
    },
    {
      "version": "v1.2.2",
      "isLatest": false,
      "createdAt": "2025-10-04T14:30:00Z",
      "contentHash": "sha256:f7e6d5c4..."
    },
    {
      "version": "v1.2.1",
      "isLatest": false,
      "createdAt": "2025-10-03T09:15:00Z",
      "contentHash": "sha256:b2a1c3d4..."
    }
  ]
}
```

### Requirements
- FR-021: Show all versions for a workflow
- Sorted by creation date (descending)
- Indicate which version is marked as latest

---

## Endpoint 4: List Namespaces

### Specification
```
GET /api/registry/namespaces
Host: localhost:9456
Accept: application/json
```

### Success Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "namespaces": [
    {
      "name": "team",
      "workflowCount": 15,
      "lastUpdated": "2025-10-05T10:00:00Z"
    },
    {
      "name": "personal",
      "workflowCount": 3,
      "lastUpdated": "2025-10-04T18:30:00Z"
    }
  ]
}
```

### Requirements
- FR-022: Support namespace organization
- Show workflow count per namespace
- Last updated timestamp

---

## Endpoint 5: Search Workflows

### Specification
```
GET /api/registry/workflows?q={query}&namespace={ns}&limit={n}
Host: localhost:9456
Accept: application/json
```

### Examples
```
GET /api/registry/workflows?q=refactor
GET /api/registry/workflows?namespace=team&limit=10
GET /api/registry/workflows?q=auth&namespace=team
```

### Success Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "results": [
    {
      "workflowRegistryId": "team/refactor-auth",
      "latestVersion": "v1.2.3",
      "namespace": "team",
      "name": "refactor-auth",
      "description": "Refactor authentication module",
      "versionCount": 3,
      "lastUpdated": "2025-10-05T10:00:00Z"
    },
    {
      "workflowRegistryId": "team/refactor-api",
      "latestVersion": "v2.0.1",
      "namespace": "team",
      "name": "refactor-api",
      "description": "API endpoint refactoring",
      "versionCount": 8,
      "lastUpdated": "2025-10-04T15:20:00Z"
    }
  ],
  "total": 2
}
```

### Parameters
- `q`: Search query (matches name or description)
- `namespace`: Filter by namespace
- `limit`: Max results (default: 50, max: 100)

---

## Endpoint 6: Update Latest Flag

### Specification
```
PATCH /api/registry/workflows/{namespace}/{name}/{version}/latest
Host: localhost:9456
Content-Type: application/json
```

### Request Body
```json
{
  "isLatest": true
}
```

### Success Response
```http
HTTP/1.1 200 OK

{
  "message": "Latest flag updated successfully",
  "workflowRegistryId": "team/refactor-auth",
  "version": "v1.2.3",
  "isLatest": true
}
```

### Requirements
- Only one version can have `isLatest=true` per (namespace, name)
- Automatically unset previous latest when setting new one

---

## Contract Test Requirements

**Note**: Test scenarios below define WHAT to validate. Python integration tests will be implemented POST-Week 5 per TDD exemption (approved 2025-10-05).

### Workflow Registration Scenarios

**Test Scenario: Manual Workflow Registration**
- **Requirement**: FR-020, FR-024
- **Given**: Valid workflow YAML and registry metadata (namespace, name, version)
- **When**: POST request to `/api/registry/workflows` with registration payload
- **Then**:
  - Response status code is 201 (Created)
  - Response contains `contentHash` field starting with "sha256:"
  - Response contains `workflowRegistryId` matching "namespace/name"
  - Record exists in workflow_version table

**Test Scenario: Duplicate Version Rejection**
- **Requirement**: FR-021
- **Given**: Workflow "team/test:v1.0.0" already exists in registry
- **When**: POST request attempting to register "team/test:v1.0.0" again
- **Then**:
  - Response status code is 409 (Conflict)
  - Error message includes "already exists"
  - No duplicate record created in database

**Test Scenario: Invalid Namespace Format Rejection**
- **Requirement**: FR-022
- **Given**: Registration request with namespace "Team-Name" (contains uppercase)
- **When**: POST request to `/api/registry/workflows`
- **Then**:
  - Response status code is 400 (Bad Request)
  - Error message mentions "namespace" format requirement
  - Error message specifies valid pattern: `^[a-z0-9_-]+$`

### Workflow Lookup Scenarios

**Test Scenario: Lookup by Specific Version**
- **Requirement**: FR-023
- **Given**: Workflow "team/test:v1.0.0" is registered
- **When**: GET request to `/api/registry/workflows/team/test/v1.0.0`
- **Then**:
  - Response status code is 200
  - Response contains workflow YAML content
  - Response `version` field equals "v1.0.0"

**Test Scenario: Lookup Using Latest Version**
- **Requirement**: FR-023
- **Given**:
  - Workflow "team/test:v1.0.0" exists with `is_latest=false`
  - Workflow "team/test:v2.0.0" exists with `is_latest=true`
- **When**: GET request to `/api/registry/workflows/team/test/latest`
- **Then**:
  - Response status code is 200
  - Response `version` field equals "v2.0.0"
  - Correct workflow YAML is returned

**Test Scenario: Missing Workflow Returns 404**
- **Requirement**: FR-035
- **Given**: Workflow "team/nonexistent:v1.0.0" does NOT exist in registry
- **When**: GET request to `/api/registry/workflows/team/nonexistent/v1.0.0`
- **Then**:
  - Response status code is 404 (Not Found)
  - Error message includes "not found"
  - Error message includes the attempted reference

**Test Scenario: Ambiguous Version Error**
- **Requirement**: FR-040
- **Given**:
  - Workflow "team/test:v1.0.0" exists with `is_latest=false`
  - Workflow "team/test:v2.0.0" exists with `is_latest=false`
  - No version has `is_latest=true`
- **When**: GET request to `/api/registry/workflows/team/test/latest`
- **Then**:
  - Response status code is 400 (Bad Request)
  - Error message includes "ambiguous"
  - Error message suggests specifying explicit version

### Version Management Scenarios

**Test Scenario: List All Workflow Versions**
- **Requirement**: FR-021
- **Given**:
  - Workflow "team/test:v1.0.0" registered at time T+0
  - Workflow "team/test:v1.1.0" registered at time T+1
  - Workflow "team/test:v2.0.0" registered at time T+2
- **When**: GET request to `/api/registry/workflows/team/test`
- **Then**:
  - Response contains `versions` array with 3 entries
  - Versions are sorted by `created_at` DESC (newest first)
  - First entry has `version: "v2.0.0"`
  - Each entry includes `isLatest` flag

---

## Database Schema

**Authoritative Source**: `scheduler/migrations/0002_add_workflow_registry.sql`

The workflow registry is implemented via the `workflow_version` table defined in migration 0002.

### Schema Summary

**Primary Key**: `(namespace, name, version)` - Composite key ensures unique versioned workflows

**Key Fields**:
- `namespace` - Organization/team identifier (lowercase alphanumeric + underscore/dash)
- `name` - Workflow name within namespace
- `version` - Version identifier (e.g., "v1.2.3", git commit hash)
- `is_latest` - Boolean flag for `:latest` version resolution
- `content_hash` - SHA-256 hash for integrity verification
- `yaml_snapshot` - Complete workflow YAML content
- Git metadata (optional): `git_repo`, `git_path`, `git_commit`, `git_branch`

**Indexes**:
- `idx_workflow_version_latest` - Accelerates `:latest` version lookups
- `idx_workflow_version_hash` - Enables content deduplication

**Foreign Keys**: None (workflows are independent entities)

**See Migration File**: `scheduler/migrations/0002_add_workflow_registry.sql` for complete DDL including constraints, indexes, and comments.

---

## Content Hash Calculation

### SHA-256 of Normalized YAML
```rust
use sha2::{Sha256, Digest};

fn calculate_content_hash(yaml: &str) -> String {
    // Normalize YAML (parse and re-serialize for canonical form)
    let workflow: serde_yaml::Value = serde_yaml::from_str(yaml)?;
    let canonical = serde_yaml::to_string(&workflow)?;

    // Calculate SHA-256
    let mut hasher = Sha256::new();
    hasher.update(canonical.as_bytes());
    let result = hasher.finalize();

    format!("sha256:{:x}", result)
}
```

### Purpose
- Detect content drift when Git sync added later
- Immutable version enforcement (reject if hash mismatch)
- Deduplication (same content, different versions)

---

## Security Considerations

**Week 5 MVP:**
- No authentication (localhost-only per NFR-005)
- No authorization (all namespaces public)
- No workflow signing/verification

**Future (Post-MVP):**
- Namespace-based access control
- Workflow signature verification
- Content-addressable storage
- Git commit signature validation

---

**Next**: quickstart.md with end-to-end workflow examples
