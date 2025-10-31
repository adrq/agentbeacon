"""Shared helpers for contract validation tests.

These utilities centralise schema loading and validation logic so multiple
contract suites can reuse the same compiled JSON Schema definitions.
"""

from __future__ import annotations

import json
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jsonschema


DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"

# Allow friendly schema aliases while still supporting explicit relative paths.
SCHEMA_NAME_MAP: Dict[str, str] = {
    "workflow": "workflow-schema.json",
    "workflow-schema": "workflow-schema.json",
    "a2a-task": "a2a-v0.3.0.schema.json",
    "a2a-task.schema": "a2a-v0.3.0.schema.json",
    "a2a": "a2a-v0.3.0.schema.json",
    "a2a-v0.3.0": "a2a-v0.3.0.schema.json",
    "message-send-params": "a2a-v0.3.0.schema.json#/definitions/MessageSendParams",
    "MessageSendParams": "a2a-v0.3.0.schema.json#/definitions/MessageSendParams",
}


def resolve_docs_path(relative_path: str) -> Path:
    """Return an absolute path within docs/ for the given relative path or alias."""

    candidate = SCHEMA_NAME_MAP.get(relative_path, relative_path)
    path = DOCS_ROOT / candidate
    if not path.exists():
        raise FileNotFoundError(f"Docs asset not found: {candidate}")
    return path


@lru_cache(maxsize=None)
def load_schema(schema_name: str) -> Dict[str, Any]:
    """Load a JSON schema from docs/ and cache the parsed dictionary.

    Supports JSON Pointer fragments (e.g., "file.json#/definitions/Type").
    When a fragment is provided, the sub-schema is wrapped with the full schema's
    definitions and $schema metadata to preserve $ref resolution.
    """

    candidate = SCHEMA_NAME_MAP.get(schema_name, schema_name)

    # Check for fragment reference (e.g., "file.json#/definitions/Type")
    if "#" in candidate:
        base_path, fragment = candidate.split("#", 1)
        path = DOCS_ROOT / base_path
        if not path.exists():
            raise FileNotFoundError(f"Docs asset not found: {base_path}")

        with path.open("r", encoding="utf-8") as handle:
            full_schema = json.load(handle)

        # Navigate to the target sub-schema
        fragment_parts = [p for p in fragment.split("/") if p]
        sub_schema = full_schema
        for part in fragment_parts:
            sub_schema = sub_schema[part]

        # Preserve definitions block and $schema for $ref resolution
        wrapped_schema = dict(sub_schema)
        if "definitions" in full_schema:
            wrapped_schema["definitions"] = full_schema["definitions"]
        if "$schema" in full_schema:
            wrapped_schema["$schema"] = full_schema["$schema"]

        return wrapped_schema

    # Existing code for non-fragment references
    path = resolve_docs_path(schema_name)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def _compiled_validator(schema_name: str) -> jsonschema.protocols.Validator:
    schema = load_schema(schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


def validate_payload(schema_name: str, payload: Any) -> None:
    """Validate payload against the named schema, raising jsonschema.ValidationError."""

    validator = _compiled_validator(schema_name)
    validator.validate(payload)


def load_json_asset(relative_path: str) -> Any:
    """Load JSON content from docs/.

    Supports both raw `.json` files and Markdown documents that embed a JSON
    code fence. Returns the parsed JSON object for downstream assertions.
    """

    path = resolve_docs_path(relative_path)
    if path.suffix in {".json", ".schema", ".schema.json"}:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    if path.suffix == ".md":
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read()
        match = re.search(r"```json\s*(.*?)```", content, flags=re.DOTALL)
        if not match:
            raise ValueError(f"No JSON code block found in {relative_path}")
        return json.loads(match.group(1))

    raise ValueError(f"Unsupported asset type for {relative_path}")


def schema_validator(schema_name: str) -> Callable[[Any], None]:
    """Return a callable that validates payloads using the given schema."""

    def _validator(payload: Any) -> None:
        validate_payload(schema_name, payload)

    return _validator


DEFAULT_WORKFLOW_REGISTRY_ID = "integration/mock-orchestrator"
DEFAULT_WORKFLOW_VERSION = "test-version-001"
DEFAULT_WORKFLOW_REF = f"{DEFAULT_WORKFLOW_REGISTRY_ID}:latest"


def build_acp_task(
    *,
    node_id: str,
    text: str,
    cwd: str,
    agent: str = "test-acp-agent",
    execution_id: Optional[str] = None,
    workflow_registry_id: str = DEFAULT_WORKFLOW_REGISTRY_ID,
    workflow_version: str = DEFAULT_WORKFLOW_VERSION,
    workflow_ref: str = DEFAULT_WORKFLOW_REF,
    protocol_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct an ACP task assignment with required metadata.cwd field.

    ACP tasks use the A2A MessageSendParams.metadata extension field to pass
    the cwd parameter required by the ACP protocol. This keeps validation enabled
    while conforming to both A2A and ACP protocol requirements.
    """
    message_id = f"{node_id}-msg-{uuid.uuid4()}"
    message = {
        "messageId": message_id,
        "kind": "message",
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
    }

    task_body = {"message": message, "metadata": {"cwd": cwd}}

    return build_canonical_task(
        node_id=node_id,
        agent=agent,
        execution_id=execution_id,
        workflow_registry_id=workflow_registry_id,
        workflow_version=workflow_version,
        workflow_ref=workflow_ref,
        protocol_metadata=protocol_metadata,
        task_body=task_body,
        validate_task=True,  # Keep validation enabled - metadata is valid per A2A schema
    )


def build_canonical_task(
    *,
    node_id: str,
    text: Optional[str] = None,
    agent: str = "mock-agent",
    execution_id: Optional[str] = None,
    workflow_registry_id: str = DEFAULT_WORKFLOW_REGISTRY_ID,
    workflow_version: str = DEFAULT_WORKFLOW_VERSION,
    workflow_ref: str = DEFAULT_WORKFLOW_REF,
    protocol_metadata: Optional[Dict[str, Any]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    task_body: Optional[Dict[str, Any]] = None,
    validate_task: bool = True,
) -> Dict[str, Any]:
    """Construct a canonical scheduler assignment matching the sync contract."""

    if task_body is None:
        message_text = text or f"Task payload for {node_id}"
        message = {
            "messageId": f"{node_id}-msg-{uuid.uuid4()}",
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": message_text}],
        }
        task_body = {"message": message}

    payload = {
        "nodeId": node_id,
        "executionId": execution_id or f"{node_id}-execution",
        "workflowRegistryId": workflow_registry_id,
        "workflowVersion": workflow_version,
        "workflowRef": workflow_ref,
        "agent": agent,
        "task": dict(task_body),
    }

    if artifacts:
        if validate_task:
            raise ValueError(
                "MessageSendParams does not support artifacts field. "
                "Set validate_task=False if testing invalid payloads."
            )
        payload["task"] = dict(payload["task"], artifacts=artifacts)

    if protocol_metadata is not None:
        payload["protocolMetadata"] = protocol_metadata

    if validate_task:
        validate_payload("message-send-params", payload["task"])

    return payload
