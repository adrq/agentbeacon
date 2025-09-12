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
    """Load a JSON schema from docs/ and cache the parsed dictionary."""

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
        task_body = {"history": [message]}

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
        payload["task"] = dict(payload["task"], artifacts=artifacts)

    if protocol_metadata is not None:
        payload["protocolMetadata"] = protocol_metadata

    if validate_task:
        validate_payload("a2a-task", payload["task"])

    return payload
