"""
ACP Protocol Contract Tests - Session/Prompt Method

These tests verify the worker's implementation of session/prompt and response handling.
Tests MUST fail initially per TDD approach (worker ACP support doesn't exist yet).

Run with: uv run pytest tests/integration/test_acp_contract_prompt.py -v
"""

import time
from pathlib import Path

import pytest
import requests

from tests.contracts.schema_helpers import build_acp_task
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


def _extract_history(task_status: dict) -> list:
    """Return Task.history list from the data part of taskStatus message."""
    message = task_status.get("message", {})
    parts = message.get("parts", [])
    for part in parts:
        if isinstance(part, dict) and part.get("kind") == "data":
            data = part.get("data")
            if isinstance(data, dict):
                return data.get("history", [])
    return []


def test_session_prompt_end_turn_success():
    """Contract test - session/prompt end_turn success.

    Verifies that worker sends session/prompt with sessionId and prompt ContentBlock array,
    receives stopReason="end_turn", and maps to A2A completed status.
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

            acp_task = build_acp_task(
                node_id="node-prompt-test",
                text="Complete this task successfully",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-prompt-test",
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

            # Verify task completed successfully (end_turn maps to completed)
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-prompt-test"]
            assert len(task_result) == 1, (
                f"Should have result for prompt test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"Task should complete successfully with end_turn: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_session_prompt_with_session_update_notifications():
    """Contract test - session/prompt with session/update notifications.

    Verifies that worker receives and accumulates session/update notifications during
    session/prompt execution and includes them in Task.history.
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

            # Use STREAM_CHUNKS special command to trigger session/update notifications
            acp_task = build_acp_task(
                node_id="node-updates-test",
                text="STREAM_CHUNKS",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-updates-test",
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

            # Verify task completed (session/update notifications should be in Task.history)
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [
                r for r in results if r["executionId"] == "exec-updates-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for updates test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"Task should complete with session/update notifications: {task_result[0]}"
            )

            # Parse and verify Task.history contains MULTIPLE session/update entries
            task_status = task_result[0]["taskStatus"]
            history = _extract_history(task_status)

            assert history and len(history) > 0, (
                f"Task.history should contain session/update notifications: {task_result[0]}"
            )
            # Verify MULTIPLE agent messages proving streaming behavior
            agent_entries = [h for h in history if h.get("role") == "agent"]
            assert len(agent_entries) >= 2, (
                f"Task.history should include MULTIPLE agent messages from STREAM_CHUNKS proving streaming: got {len(agent_entries)} messages, expected >=2. History: {history}"
            )

        finally:
            cleanup_processes(processes)


def test_session_prompt_cancelled():
    """Contract test - session/prompt canceled (stop_reason=cancelled).

    Verifies that worker handles session/prompt response with stopReason="cancelled"
    and maps to A2A canceled status.
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

            # Task with delay that can be canceled
            acp_task = build_acp_task(
                node_id="node-cancel-test",
                text="DELAY_5",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-cancel-test",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            # Wait for worker to pick up task and start session/prompt
            time.sleep(2)
            # Send cancel command via worker sync protocol
            cancel_command = {
                "executionId": "exec-cancel-test",
                "nodeId": "node-cancel-test",
                "command": "cancel",
            }
            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/test/add_command",
                json=cancel_command,
            )
            assert response.status_code == 200

            # Wait for worker to process cancellation
            time.sleep(3)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify task mapped to canceled status
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-cancel-test"]
            assert len(task_result) == 1, (
                f"Should have result for cancel test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] == "canceled", (
                f"Task should be canceled (not failed): {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_session_prompt_error():
    """Contract test - session/prompt error (stop_reason=error).

    Verifies that worker handles session/prompt response with stopReason="error"
    and maps to A2A failed status with error message.
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

            # Use FAIL_NODE special command to trigger error stopReason
            acp_task = build_acp_task(
                node_id="node-error-test",
                text="FAIL_NODE",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-error-test",
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

            # Verify task failed (error stopReason maps to failed status)
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-error-test"]
            assert len(task_result) == 1, (
                f"Should have result for error test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail when stopReason is error: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)
