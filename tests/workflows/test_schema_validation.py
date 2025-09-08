"""Schema validation tests for workflow documentation examples."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_SCHEMA_PATH = ROOT / "docs" / "workflow-schema.json"
A2A_SCHEMA_PATH = ROOT / "docs" / "a2a-task.schema.json"
AGENTS_SCHEMA_PATH = ROOT / "docs" / "agents-schema.json"
SEQUENTIAL_EXAMPLE_PATH = ROOT / "examples" / "workflow_sequential.yaml"
PARALLEL_EXAMPLE_PATH = ROOT / "examples" / "workflow_parallel.yaml"
SIMPLE_EXAMPLE_PATH = ROOT / "examples" / "simple.yaml"
PARALLEL_NEW_SPEC_PATH = ROOT / "examples" / "parallel.yaml"
AGENTS_EXAMPLE_PATH = ROOT / "examples" / "agents.yaml"


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


def _build_workflow_validator() -> Draft202012Validator:
    workflow_schema = _load_json_schema(WORKFLOW_SCHEMA_PATH)
    a2a_schema = _load_json_schema(A2A_SCHEMA_PATH)

    workflow_schema_id = workflow_schema.get(
        "$id", "https://schemas.agentmaestro.dev/workflow-schema.json"
    )
    a2a_schema_id = a2a_schema.get(
        "$id", "https://agentmaestro.dev/schemas/a2a-task.json"
    )

    registry = Registry().with_resources(
        [
            (workflow_schema_id, Resource.from_contents(workflow_schema)),
            (a2a_schema_id, Resource.from_contents(a2a_schema)),
        ]
    )

    return Draft202012Validator(workflow_schema, registry=registry)


def _build_agents_validator() -> Draft202012Validator:
    agents_schema = _load_json_schema(AGENTS_SCHEMA_PATH)
    return Draft202012Validator(agents_schema)


def _assert_artifact_contract(document: dict) -> None:
    tasks = document.get("tasks", [])
    if not isinstance(tasks, list):
        pytest.fail("Workflow document must define a list of tasks")

    task_ids: set[str] = set()
    artifact_producers: dict[str, str] = {}
    task_dependencies: dict[str, set[str]] = {}

    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            pytest.fail(f"tasks[{index}] is not an object")

        task_id = task.get("id")
        if not isinstance(task_id, str):
            pytest.fail(f"tasks[{index}] missing string id")

        if task_id in task_ids:
            pytest.fail(f"Duplicate task id detected: {task_id}")

        task_ids.add(task_id)

        depends_on = task.get("depends_on", []) or []
        if not isinstance(depends_on, list):
            pytest.fail(f"tasks[{index}].depends_on must be a list when present")
        task_dependencies[task_id] = {dep for dep in depends_on if isinstance(dep, str)}

        task_block = task.get("task")
        artifacts = []
        if isinstance(task_block, dict):
            artifacts = task_block.get("artifacts", []) or []

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                pytest.fail(f"tasks[{index}].task.artifacts entries must be objects")

            artifact_id = artifact.get("artifactId")
            if not isinstance(artifact_id, str):
                pytest.fail(f"Artifact on task {task_id} missing string artifactId")

            if artifact_id in artifact_producers:
                pytest.fail(
                    f"Artifact id '{artifact_id}' declared by task {task_id} already provided by {artifact_producers[artifact_id]}"
                )

            artifact_producers[artifact_id] = task_id

    for index, task in enumerate(tasks):
        task_id = task.get("id")
        inputs_section = task.get("inputs")
        if not inputs_section:
            continue

        if not isinstance(inputs_section, dict):
            pytest.fail(f"tasks[{index}].inputs must be an object when present")

        input_artifacts = inputs_section.get("artifacts", []) or []
        if not isinstance(input_artifacts, list):
            pytest.fail(f"tasks[{index}].inputs.artifacts must be a list when present")

        for ref_index, reference in enumerate(input_artifacts):
            if not isinstance(reference, dict):
                pytest.fail(
                    f"tasks[{index}].inputs.artifacts[{ref_index}] must be an object"
                )

            from_task = reference.get("from")
            artifact_id = reference.get("artifactId")

            if not isinstance(from_task, str) or from_task not in task_ids:
                pytest.fail(
                    f"tasks[{index}].inputs.artifacts[{ref_index}] references unknown task {from_task!r}"
                )

            if not isinstance(artifact_id, str):
                pytest.fail(
                    f"tasks[{index}].inputs.artifacts[{ref_index}] missing string artifactId"
                )

            producer = artifact_producers.get(artifact_id)
            if producer is None:
                pytest.fail(
                    f"tasks[{index}] expects artifact '{artifact_id}' but no task declares it"
                )

            if producer != from_task:
                pytest.fail(
                    f"tasks[{index}] expects artifact '{artifact_id}' from task '{from_task}', but it is produced by '{producer}'"
                )

            if from_task == task_id:
                pytest.fail(
                    f"tasks[{index}] lists its own artifact '{artifact_id}' as an input; outputs are implicit"
                )

            if from_task not in task_dependencies.get(task_id, set()):
                pytest.fail(
                    f"tasks[{index}] must declare depends_on: {from_task} when consuming artifact '{artifact_id}'"
                )


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
    validator.validate(document)
    _assert_artifact_contract(document)


def test_agents_yaml_validates_against_schema() -> None:
    """Test that examples/agents.yaml validates against the agents schema."""
    validator = _build_agents_validator()
    document = _load_yaml_document(AGENTS_EXAMPLE_PATH)
    validator.validate(document)
