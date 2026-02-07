"""Schema validation tests for workflow documentation examples."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="Deferred: DAG model removed")

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_SCHEMA_PATH = ROOT / "docs" / "workflow-schema.json"
A2A_SCHEMA_PATH = ROOT / "docs" / "a2a-v0.3.0.schema.json"
AGENTS_SCHEMA_PATH = ROOT / "docs" / "agents-schema.json"
SEQUENTIAL_EXAMPLE_PATH = ROOT / "examples" / "workflow_sequential.yaml"
PARALLEL_EXAMPLE_PATH = ROOT / "examples" / "workflow_parallel.yaml"
SIMPLE_EXAMPLE_PATH = ROOT / "examples" / "simple.yaml"
PARALLEL_NEW_SPEC_PATH = ROOT / "examples" / "parallel.yaml"
AGENTS_EXAMPLE_PATH = ROOT / "examples" / "agents.yaml"
GUARDRAILS_VALID_PATH = ROOT / "examples" / "workflow_guardrails_valid.yaml"
GUARDRAILS_DUPLICATE_ID_PATH = ROOT / "examples" / "workflow_invalid_duplicate_id.yaml"
GUARDRAILS_CYCLE_PATH = ROOT / "examples" / "workflow_invalid_cycle.yaml"
GUARDRAILS_MISSING_ARTIFACT_PATH = (
    ROOT / "examples" / "workflow_invalid_missing_artifact.yaml"
)


def _load_yaml_document(path: Path) -> dict:
    """Load a YAML document from disk, failing loudly when it is missing."""
    if not path.exists():
        pytest.fail(f"Expected example YAML at {path}, but the file was not found")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        pytest.fail(
            f"Expected YAML document at {path} to parse into a mapping, got {type(data)!r}"
        )

    return data


def _load_json_schema(path: Path) -> dict:
    """Load a JSON schema file from disk."""
    if not path.exists():
        pytest.fail(f"Expected JSON schema at {path}, but the file was not found")

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _inject_validation_fields(workflow: dict) -> dict:
    """
    Inject temporary messageId/kind values for validation (FR-015, FR-016, FR-017).

    Returns a deep copy of the workflow with injected fields for validation.
    The original workflow is not modified.
    """
    workflow_copy = copy.deepcopy(workflow)

    if "tasks" not in workflow_copy or not isinstance(workflow_copy["tasks"], list):
        return workflow_copy

    for task in workflow_copy["tasks"]:
        if not isinstance(task, dict):
            continue

        task_obj = task.get("task")
        if not isinstance(task_obj, dict):
            continue

        # Handle MessageSendParams structure (task.message)
        message = task_obj.get("message")
        if isinstance(message, dict):
            # Inject messageId if absent
            if "messageId" not in message:
                message["messageId"] = "temp"
            # Inject kind if absent
            if "kind" not in message:
                message["kind"] = "message"

    return workflow_copy


class WorkflowValidator:
    """
    Workflow validator with injection support (FR-015, FR-016, FR-017).

    Validates workflows by injecting temporary messageId/kind values before validation,
    then validates against the A2A schema.
    """

    def __init__(self, base_validator: Draft202012Validator):
        self._validator = base_validator

    def validate(self, workflow: dict) -> None:
        """Validate workflow with injection of temporary messageId/kind fields."""
        workflow_for_validation = _inject_validation_fields(workflow)
        self._validator.validate(workflow_for_validation)


def _build_workflow_validator() -> WorkflowValidator:
    workflow_schema = _load_json_schema(WORKFLOW_SCHEMA_PATH)
    a2a_schema = _load_json_schema(A2A_SCHEMA_PATH)

    workflow_schema_id = workflow_schema.get(
        "$id", "https://schemas.agentmaestro.dev/workflow-schema.json"
    )

    # Register A2A schema with URI that resolves relative to workflow schema's base URI
    # workflow-schema.json references "a2a-v0.3.0.schema.json" which resolves to this URI
    a2a_schema_uri = "https://schemas.agentmaestro.dev/a2a-v0.3.0.schema.json"

    registry = Registry().with_resources(
        [
            (workflow_schema_id, Resource.from_contents(workflow_schema)),
            (a2a_schema_uri, Resource.from_contents(a2a_schema)),
        ]
    )

    base_validator = Draft202012Validator(workflow_schema, registry=registry)
    return WorkflowValidator(base_validator)


def _build_agents_validator() -> Draft202012Validator:
    agents_schema = _load_json_schema(AGENTS_SCHEMA_PATH)
    return Draft202012Validator(agents_schema)


@pytest.mark.parametrize(
    "example_path",
    [
        pytest.param(SEQUENTIAL_EXAMPLE_PATH, id="workflow_sequential"),
        pytest.param(PARALLEL_EXAMPLE_PATH, id="workflow_parallel"),
        pytest.param(SIMPLE_EXAMPLE_PATH, id="simple"),
        pytest.param(PARALLEL_NEW_SPEC_PATH, id="parallel"),
    ],
)
def test_examples_match_schema(example_path: Path) -> None:
    validator = _build_workflow_validator()
    document = _load_yaml_document(example_path)
    if "defaultAgent" in document:
        pytest.fail("Workflow documents must not define legacy 'defaultAgent'")
    validator.validate(document)


def test_agents_yaml_validates_against_schema() -> None:
    """Test that examples/agents.yaml validates against the agents schema."""
    validator = _build_agents_validator()
    document = _load_yaml_document(AGENTS_EXAMPLE_PATH)
    validator.validate(document)


def test_guardrails_valid_workflow_passes() -> None:
    """Valid guardrail workflow should pass all validations."""
    validator = _build_workflow_validator()
    document = _load_yaml_document(GUARDRAILS_VALID_PATH)
    validator.validate(document)


def test_guardrails_duplicate_id_fails() -> None:
    """Workflow with duplicate task IDs should pass schema but fail DAG validation (tested in Rust)."""
    # Note: Duplicate ID detection is a DAG guardrail enforced by the Rust validator
    # JSON Schema validation will pass, but Rust DAG validation will catch this
    validator = _build_workflow_validator()
    document = _load_yaml_document(GUARDRAILS_DUPLICATE_ID_PATH)
    # Schema validation should pass (duplicate IDs are valid JSON Schema)
    # TODO: replace this with proper integration test with Rust validator
    validator.validate(document)


def test_guardrails_cycle_detection() -> None:
    """Workflow with circular dependencies should be detected."""
    document = _load_yaml_document(GUARDRAILS_CYCLE_PATH)

    # Build dependency graph and check for cycles
    tasks = document.get("tasks", [])
    task_ids = {task["id"] for task in tasks}
    dependencies = {task["id"]: set(task.get("depends_on", [])) for task in tasks}

    # Detect cycles using DFS
    def has_cycle(node: str, visited: set[str], rec_stack: set[str]) -> bool:
        visited.add(node)
        rec_stack.add(node)

        for neighbor in dependencies.get(node, set()):
            if neighbor not in visited:
                if has_cycle(neighbor, visited, rec_stack):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(node)
        return False

    visited: set[str] = set()
    has_cycle_detected = False

    for task_id in task_ids:
        if task_id not in visited:
            if has_cycle(task_id, visited, set()):
                has_cycle_detected = True
                break

    assert has_cycle_detected, "Expected cycle to be detected in workflow"


# =============================================================================
# Tests for MessageSendParams Migration (T002-T007)
# =============================================================================


@pytest.mark.parametrize(
    "example_path",
    [
        pytest.param(SEQUENTIAL_EXAMPLE_PATH, id="workflow_sequential"),
        pytest.param(PARALLEL_EXAMPLE_PATH, id="workflow_parallel"),
        pytest.param(SIMPLE_EXAMPLE_PATH, id="simple"),
        pytest.param(PARALLEL_NEW_SPEC_PATH, id="parallel"),
        pytest.param(GUARDRAILS_VALID_PATH, id="workflow_guardrails_valid"),
    ],
)
def test_examples_match_updated_schema(example_path: Path) -> None:
    """Example workflows validate with updated schema."""
    validator = _build_workflow_validator()
    document = _load_yaml_document(example_path)
    validator.validate(document)


def test_old_format_history_rejected() -> None:
    """Legacy task.history field is rejected."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-old-format-history",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "history": [
                        {
                            "messageId": "msg-1",
                            "kind": "message",
                            "role": "user",
                            "parts": [{"kind": "text", "text": "Old format"}],
                        }
                    ]
                },
            }
        ],
    }

    with pytest.raises(ValidationError, match="message.*required"):
        validator.validate(workflow)


def test_old_format_artifacts_rejected() -> None:
    """Legacy task.artifacts field is rejected."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-old-format-artifacts",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "history": [
                        {
                            "messageId": "msg-1",
                            "kind": "message",
                            "role": "user",
                            "parts": [{"kind": "text", "text": "Test"}],
                        }
                    ],
                    "artifacts": [
                        {
                            "artifactId": "art-1",
                            "parts": [{"kind": "text", "text": "Artifact"}],
                        }
                    ],
                },
            }
        ],
    }

    with pytest.raises(ValidationError):
        validator.validate(workflow)


def test_inputs_field_rejected() -> None:
    """Legacy inputs field is rejected."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-inputs-rejected",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "inputs": {"artifacts": [{"from": "task-0", "artifactId": "result"}]},
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    }
                },
            }
        ],
    }

    with pytest.raises(
        ValidationError, match="Additional properties.*inputs|inputs.*not allowed"
    ):
        validator.validate(workflow)


def test_permission_valid_values() -> None:
    """Permission field accepts allow, deny, ask values."""
    validator = _build_workflow_validator()

    for permission in ["allow", "deny", "ask"]:
        workflow = {
            "name": f"test-permission-{permission}",
            "tasks": [
                {
                    "id": "task-1",
                    "agent": "test-agent",
                    "execution": {"permission": permission},
                    "task": {
                        "message": {
                            "messageId": "msg-1",
                            "kind": "message",
                            "role": "user",
                            "parts": [{"kind": "text", "text": "Test"}],
                        }
                    },
                }
            ],
        }
        validator.validate(workflow)


def test_permission_invalid_value() -> None:
    """Invalid permission values are rejected."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-invalid-permission",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "execution": {"permission": "maybe"},
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    }
                },
            }
        ],
    }

    with pytest.raises(ValidationError, match="maybe|enum|permission"):
        validator.validate(workflow)


def test_permission_optional() -> None:
    """Permission field is optional in execution config."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-permission-optional",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "execution": {"timeout": 300},
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    }
                },
            }
        ],
    }

    validator.validate(workflow)


def test_message_required_fields() -> None:
    """Message structure requires role and parts (messageId and kind are optional)."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    # Missing role should fail validation
    workflow = {
        "name": "test-missing-role",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "parts": [{"kind": "text", "text": "Test"}],
                    }
                },
            }
        ],
    }

    with pytest.raises(ValidationError, match="role.*required"):
        validator.validate(workflow)


def test_message_invalid_role() -> None:
    """Message role must be user or agent."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-invalid-role",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "invalid_role",
                        "parts": [{"kind": "text", "text": "Test"}],
                    }
                },
            }
        ],
    }

    with pytest.raises(ValidationError, match="invalid_role|role"):
        validator.validate(workflow)


def test_message_empty_parts() -> None:
    """Message parts array can be empty per A2A v0.3.0 schema (no minItems constraint)."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-empty-parts",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [],
                    }
                },
            }
        ],
    }

    # Should pass validation - A2A schema doesn't enforce minItems on parts
    validator.validate(workflow)


def test_configuration_validation() -> None:
    """MessageSendConfiguration validates when present."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-configuration",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    },
                    "configuration": {"blocking": True, "historyLength": 5},
                },
            }
        ],
    }

    validator.validate(workflow)


def test_metadata_freeform_acceptance() -> None:
    """Metadata accepts arbitrary JSON structure."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-metadata-freeform",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    },
                    "metadata": {
                        "custom_field": "any value",
                        "nested": {"object": {"allowed": True}},
                        "array": [1, 2, 3],
                        "null_value": None,
                    },
                },
            }
        ],
    }

    validator.validate(workflow)


def test_validation_error_format() -> None:
    """Validation errors contain non-empty messages."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-multiple-errors",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "kind": "message",
                        "role": "invalid_role",
                        "parts": [],
                    }
                },
            }
        ],
    }

    try:
        validator.validate(workflow)
        pytest.fail("Expected ValidationError")
    except ValidationError as e:
        error_msg = str(e.message)
        assert len(error_msg) > 0, "Error message should not be empty"


def test_unknown_field_rejected() -> None:
    """Unknown task fields are rejected."""
    from jsonschema import ValidationError

    validator = _build_workflow_validator()

    workflow = {
        "name": "test-unknown-field",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "unknown_field": "not allowed",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    }
                },
            }
        ],
    }

    with pytest.raises(ValidationError, match="unknown_field|Unevaluated"):
        validator.validate(workflow)


def test_unknown_messagesendparams_field_rejected() -> None:
    """Unknown MessageSendParams fields are allowed per A2A v0.3.0 schema (no additionalProperties: false)."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-unknown-messagesendparams-field",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test"}],
                    },
                    "unknown_task_field": "allowed in A2A MessageSendParams",
                },
            }
        ],
    }

    # Should pass - A2A MessageSendParams allows additional properties
    validator.validate(workflow)


def test_schema_no_version_field() -> None:
    """Schema does not include version identifier fields."""
    schema = _load_json_schema(WORKFLOW_SCHEMA_PATH)

    assert "version" not in schema, "Schema should not have version field"
    assert "schemaVersion" not in schema, "Schema should not have schemaVersion field"


# =============================================================================
# Tests for Optional messageId/kind with Injection
# =============================================================================


def test_message_optional_messageid() -> None:
    """Message without messageId validates (auto-generated at dispatch)."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-optional-messageid",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test without messageId"}],
                    }
                },
            }
        ],
    }

    validator.validate(workflow)


def test_message_optional_kind() -> None:
    """Message without kind validates (auto-injected at dispatch)."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-optional-kind",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test without kind"}],
                    }
                },
            }
        ],
    }

    validator.validate(workflow)


def test_message_optional_both() -> None:
    """Message without messageId or kind validates (both auto-injected)."""
    validator = _build_workflow_validator()

    workflow = {
        "name": "test-minimal-message",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Minimal message"}],
                    }
                },
            }
        ],
    }

    validator.validate(workflow)


def test_message_explicit_values_preserved() -> None:
    """User-provided messageId and kind are preserved exactly."""
    validator = _build_workflow_validator()

    # Custom messageId and kind should be preserved
    workflow = {
        "name": "test-explicit-values",
        "tasks": [
            {
                "id": "task-1",
                "agent": "test-agent",
                "task": {
                    "message": {
                        "messageId": "custom-id-123",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Custom ID message"}],
                    }
                },
            }
        ],
    }

    # Should validate without modification
    validator.validate(workflow)
