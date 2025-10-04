"""API contract tests for workflow validation endpoint responses.

These tests validate against the SPEC contract (not current Rust implementation):
- POST /api/workflows/validate returns HTTP 422 with {"status": "error", "issues": [...]}
- POST /api/workflows returns HTTP 422 with {"status": "error", "issues": [...]}

Current Rust returns HTTP 200 with {"valid": false, "errors": [...]} but will be updated.
Tests expect SPEC behavior since implementation will change to match spec.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from tests.testhelpers import scheduler_context

ROOT = Path(__file__).resolve().parents[2]
GUARDRAILS_DUPLICATE_ID_PATH = ROOT / "examples" / "workflow_invalid_duplicate_id.yaml"
GUARDRAILS_CYCLE_PATH = ROOT / "examples" / "workflow_invalid_cycle.yaml"
GUARDRAILS_ARTIFACT_PATH = ROOT / "examples" / "workflow_invalid_artifact.yaml"
GUARDRAILS_MISSING_ARTIFACT_PATH = (
    ROOT / "examples" / "workflow_invalid_missing_artifact.yaml"
)
GUARDRAILS_VALID_PATH = ROOT / "examples" / "workflow_guardrails_valid.yaml"


def _load_yaml_as_string(path: Path) -> str:
    """Load a YAML file as a string."""
    if not path.exists():
        pytest.fail(f"Expected example YAML at {path}, but the file was not found")

    with path.open("r", encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_validation_endpoint_returns_422_for_duplicate_id(test_database) -> None:
    """POST /api/workflows/validate should return HTTP 422 for duplicate task IDs."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        yaml_content = _load_yaml_as_string(GUARDRAILS_DUPLICATE_ID_PATH)

        response = requests.post(
            f"{scheduler_url}/api/workflows/validate",
            headers={"Content-Type": "application/json"},
            json={"yaml": yaml_content},
        )

        assert response.status_code == 422, (
            f"Expected HTTP 422, got {response.status_code}"
        )

        payload = response.json()
        assert payload.get("status") == "error", (
            f"Expected status='error', got: {payload}"
        )
        assert "issues" in payload, "Expected 'issues' field in response"
        assert isinstance(payload["issues"], list), "Expected 'issues' to be a list"
        assert len(payload["issues"]) > 0, "Expected at least one issue in the list"

        # Verify error message mentions duplicate task ID
        issues_text = " ".join(str(issue) for issue in payload["issues"])
        assert (
            "duplicate" in issues_text.lower() or "draft_welcome" in issues_text.lower()
        ), f"Expected error about duplicate task ID, got: {payload['issues']}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_validation_endpoint_returns_422_for_cycle(test_database) -> None:
    """POST /api/workflows/validate should return HTTP 422 for circular dependencies."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        yaml_content = _load_yaml_as_string(GUARDRAILS_CYCLE_PATH)

        response = requests.post(
            f"{scheduler_url}/api/workflows/validate",
            headers={"Content-Type": "application/json"},
            json={"yaml": yaml_content},
        )

        assert response.status_code == 422, (
            f"Expected HTTP 422, got {response.status_code}"
        )

        payload = response.json()
        assert payload.get("status") == "error", (
            f"Expected status='error', got: {payload}"
        )
        assert "issues" in payload, "Expected 'issues' field in response"
        assert isinstance(payload["issues"], list), "Expected 'issues' to be a list"
        assert len(payload["issues"]) > 0, "Expected at least one issue in the list"

        # Verify error message mentions cycle
        issues_text = " ".join(str(issue) for issue in payload["issues"])
        assert "cycle" in issues_text.lower() or "circular" in issues_text.lower(), (
            f"Expected error about cycle, got: {payload['issues']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_validation_endpoint_returns_422_for_artifact_violation(test_database) -> None:
    """POST /api/workflows/validate should return HTTP 422 for artifact dependency violations."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        yaml_content = _load_yaml_as_string(GUARDRAILS_ARTIFACT_PATH)

        response = requests.post(
            f"{scheduler_url}/api/workflows/validate",
            headers={"Content-Type": "application/json"},
            json={"yaml": yaml_content},
        )

        assert response.status_code == 422, (
            f"Expected HTTP 422, got {response.status_code}"
        )

        payload = response.json()
        assert payload.get("status") == "error", (
            f"Expected status='error', got: {payload}"
        )
        assert "issues" in payload, "Expected 'issues' field in response"
        assert isinstance(payload["issues"], list), "Expected 'issues' to be a list"
        assert len(payload["issues"]) > 0, "Expected at least one issue in the list"

        # Verify error message mentions artifact and dependency
        issues_text = " ".join(str(issue) for issue in payload["issues"])
        assert (
            "artifact" in issues_text.lower() and "depend" in issues_text.lower()
        ) or "welcome_note" in issues_text.lower(), (
            f"Expected error about artifact dependency, got: {payload['issues']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_validation_endpoint_returns_422_for_missing_artifact(test_database) -> None:
    """POST /api/workflows/validate should return HTTP 422 for non-existent artifacts."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        yaml_content = _load_yaml_as_string(GUARDRAILS_MISSING_ARTIFACT_PATH)

        response = requests.post(
            f"{scheduler_url}/api/workflows/validate",
            headers={"Content-Type": "application/json"},
            json={"yaml": yaml_content},
        )

        assert response.status_code == 422, (
            f"Expected HTTP 422, got {response.status_code}"
        )

        payload = response.json()
        assert payload.get("status") == "error", (
            f"Expected status='error', got: {payload}"
        )
        assert "issues" in payload, "Expected 'issues' field in response"
        assert isinstance(payload["issues"], list), "Expected 'issues' to be a list"
        assert len(payload["issues"]) > 0, "Expected at least one issue in the list"

        # Verify error message mentions missing artifact
        issues_text = " ".join(str(issue) for issue in payload["issues"])
        assert (
            "artifact" in issues_text.lower()
            and "no task declares" in issues_text.lower()
        ) or "nonexistent_artifact" in issues_text.lower(), (
            f"Expected error about missing artifact, got: {payload['issues']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_validation_endpoint_returns_200_for_valid_workflow(test_database) -> None:
    """POST /api/workflows/validate should return HTTP 200 with status='ok' for valid workflows."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        yaml_content = _load_yaml_as_string(GUARDRAILS_VALID_PATH)

        response = requests.post(
            f"{scheduler_url}/api/workflows/validate",
            headers={"Content-Type": "application/json"},
            json={"yaml": yaml_content},
        )

        assert response.status_code == 200, (
            f"Expected HTTP 200, got {response.status_code}"
        )

        payload = response.json()
        assert payload.get("status") == "ok", f"Expected status='ok', got: {payload}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_workflow_creation_endpoint_returns_422_for_invalid_workflow(
    test_database,
) -> None:
    """POST /api/workflows should return HTTP 422 for invalid workflows before persistence."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        yaml_content = _load_yaml_as_string(GUARDRAILS_DUPLICATE_ID_PATH)

        response = requests.post(
            f"{scheduler_url}/api/workflows",
            headers={"Content-Type": "application/json"},
            json={"yaml_content": yaml_content},
        )

        # Should reject with 422 (or possibly 400 depending on how Rust handles it)
        assert response.status_code in [400, 422], (
            f"Expected HTTP 400 or 422 for invalid workflow, got {response.status_code}"
        )

        payload = response.json()
        # Response should contain error information (format depends on Rust error handling)
        assert "error" in payload or "message" in payload or "status" in payload, (
            f"Expected error information in response, got: {payload}"
        )
