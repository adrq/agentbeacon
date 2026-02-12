"""
ACP Protocol Contract Tests - ACP Protocol Contract Tests - Session Methods

These tests verify the worker's implementation of session/new and session lifecycle.
Tests MUST fail initially per TDD approach (worker ACP support doesn't exist yet).

Run with: uv run pytest tests/integration/test_acp_contract_session.py -v
"""

import time
from pathlib import Path

import pytest
import requests

from tests.contracts.schema_helpers import build_acp_task, build_canonical_task
from tests.testhelpers import (
    cleanup_processes,
    start_mock_scheduler,
    wait_for_port,
    start_worker,
    PortManager,
)

pytestmark = pytest.mark.skip(
    reason="Disabled: uses old worker sync protocol. Re-enable after full ACP support."
)


def test_session_new_success():
    """Contract test - Contract test - session/new success with cwd and mcpServers.

     Verifies that worker sends session/new with absolute cwd path and mcpServers=[] empty array
    .
    """
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # ACP task with valid absolute cwd path
            acp_task = build_acp_task(
                node_id="node-session-test",
                text="Test session creation",
                cwd="/tmp/valid-absolute-path",
                agent="test-acp-agent",
                execution_id="exec-session-test",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(3)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify task completed successfully
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [
                r for r in results if r["executionId"] == "exec-session-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for session test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"Task should complete successfully: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_session_new_invalid_cwd_missing():
    """Contract test - Contract test - session/new with missing cwd field.

    Verifies that worker fails task with 'Missing cwd' error when task.metadata.cwd is absent.
    Task should fail fast before subprocess spawn.
    """
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # ACP task WITHOUT cwd in metadata - test invalid scenario
            acp_task = build_canonical_task(
                node_id="node-missing-cwd",
                text="Test missing cwd",
                agent="test-acp-agent",
                execution_id="exec-missing-cwd",
                task_body={
                    "message": {
                        "messageId": "node-missing-cwd-msg",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test missing cwd"}],
                    },
                    "metadata": {},  # No cwd field - invalid for ACP
                },
                validate_task=True,
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(2)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify scheduler recorded failure
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-missing-cwd"]
            assert len(task_result) == 1, (
                f"Should have result for missing cwd test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail: {task_result[0]}"
            )

            # Verify error message mentions cwd validation failure (flexible wording)
            # Error text is in taskStatus.message.parts[0].text per A2ATaskStatus::failed
            error_message = task_result[0]["taskStatus"].get("message", {})
            error_text = ""
            if isinstance(error_message, dict):
                parts = error_message.get("parts", [])
                if parts and len(parts) > 0:
                    first_part = parts[0]
                    if "text" in first_part:
                        error_text = first_part["text"].lower()
                    else:
                        error_text = str(first_part).lower()
            else:
                error_text = str(error_message).lower()
            # Accept any message mentioning "cwd" and indicating it's missing/required
            has_cwd = "cwd" in error_text
            has_missing_indicator = any(
                word in error_text
                for word in ["missing", "required", "not found", "absent", "must"]
            )
            assert has_cwd and has_missing_indicator, (
                f"Error should indicate cwd is missing/required: {error_text}"
            )

            # Verify fast failure - no subprocess spawned (minimal/empty history)
            history = task_result[0]["taskStatus"].get("history", [])
            assert len(history) <= 1, (
                f"History should be minimal (no agent interaction) fast failure: {history}"
            )

        finally:
            cleanup_processes(processes)


def test_session_new_invalid_cwd_relative():
    """Contract test - Contract test - session/new with relative path cwd.

    Verifies that worker fails task with 'cwd must be absolute path' error when cwd is relative.
    Task should fail fast before subprocess spawn.
    """
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # ACP task with relative path cwd - test invalid scenario
            acp_task = build_canonical_task(
                node_id="node-relative-cwd",
                text="Test relative cwd",
                agent="test-acp-agent",
                execution_id="exec-relative-cwd",
                task_body={
                    "message": {
                        "messageId": "node-relative-cwd-msg",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test relative cwd"}],
                    },
                    "metadata": {
                        "cwd": "./relative/path"  # Relative path - invalid for ACP
                    },
                },
                validate_task=True,
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(2)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify scheduler recorded failure
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [
                r for r in results if r["executionId"] == "exec-relative-cwd"
            ]
            assert len(task_result) == 1, (
                f"Should have result for relative cwd test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail: {task_result[0]}"
            )

            # Verify error message mentions absolute path requirement (flexible wording)
            # Error text is in taskStatus.message.parts[0].text per A2ATaskStatus::failed
            error_message = task_result[0]["taskStatus"].get("message", {})
            error_text = ""
            if isinstance(error_message, dict):
                parts = error_message.get("parts", [])
                if parts and len(parts) > 0:
                    first_part = parts[0]
                    if "text" in first_part:
                        error_text = first_part["text"].lower()
                    else:
                        error_text = str(first_part).lower()
            else:
                error_text = str(error_message).lower()
            # Accept any message mentioning "cwd" and "absolute" path requirement
            has_cwd = "cwd" in error_text
            has_absolute = "absolute" in error_text or (
                "must" in error_text and "path" in error_text
            )
            assert has_cwd and has_absolute, (
                f"Error should indicate cwd must be absolute path: {error_text}"
            )

            # Verify fast failure - no subprocess spawned (minimal/empty history)
            history = task_result[0]["taskStatus"].get("history", [])
            assert len(history) <= 1, (
                f"History should be minimal (no agent interaction) fast failure: {history}"
            )

        finally:
            cleanup_processes(processes)


def test_session_new_invalid_cwd_empty():
    """Contract test - Contract test - session/new with empty string cwd.

    Verifies that worker fails task with 'Missing cwd' error when cwd is empty string.
    Task should fail fast before subprocess spawn.
    """
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # ACP task with empty string cwd - test invalid scenario
            acp_task = build_canonical_task(
                node_id="node-empty-cwd",
                text="Test empty cwd",
                agent="test-acp-agent",
                execution_id="exec-empty-cwd",
                task_body={
                    "message": {
                        "messageId": "node-empty-cwd-msg",
                        "kind": "message",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Test empty cwd"}],
                    },
                    "metadata": {
                        "cwd": ""  # Empty string - invalid for ACP
                    },
                },
                validate_task=True,
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(2)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify scheduler recorded failure
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-empty-cwd"]
            assert len(task_result) == 1, (
                f"Should have result for empty cwd test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail: {task_result[0]}"
            )

            # Verify error message mentions cwd validation failure (flexible wording)
            # Error text is in taskStatus.message.parts[0].text per A2ATaskStatus::failed
            error_message = task_result[0]["taskStatus"].get("message", {})
            error_text = ""
            if isinstance(error_message, dict):
                parts = error_message.get("parts", [])
                if parts and len(parts) > 0:
                    first_part = parts[0]
                    if "text" in first_part:
                        error_text = first_part["text"].lower()
                    else:
                        error_text = str(first_part).lower()
            else:
                error_text = str(error_message).lower()
            # Accept any message mentioning "cwd" and indicating it's missing/required (empty string = missing)
            has_cwd = "cwd" in error_text
            has_missing_indicator = any(
                word in error_text
                for word in [
                    "missing",
                    "required",
                    "not found",
                    "absent",
                    "empty",
                    "must",
                ]
            )
            assert has_cwd and has_missing_indicator, (
                f"Error should indicate cwd is missing/required: {error_text}"
            )

            # Verify fast failure - no subprocess spawned (minimal/empty history)
            history = task_result[0]["taskStatus"].get("history", [])
            assert len(history) <= 1, (
                f"History should be minimal (no agent interaction) fast failure: {history}"
            )

        finally:
            cleanup_processes(processes)
