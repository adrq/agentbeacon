"""
T036-T041: ACP Quickstart Integration Tests

These tests verify end-to-end scenarios from quickstart.md for ACP protocol support.
Tests MUST fail initially per TDD approach (worker ACP support doesn't exist yet).

Run with: uv run pytest tests/integration/test_acp_quickstart.py -v
"""

import time
from pathlib import Path

import requests

from tests.contracts.schema_helpers import build_acp_task, build_canonical_task
from tests.testhelpers import (
    cleanup_processes,
    start_mock_scheduler,
    wait_for_port,
    start_worker,
    start_and_wait_for_a2a_agent,
    PortManager,
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


def test_quickstart_scenario_1_basic_acp_task():
    """T036: Quickstart scenario 1 - Basic ACP task execution.

    Verifies end-to-end flow: scheduler assigns ACP task → worker spawns agent subprocess
    → complete ACP protocol sequence → task completes → result returned to scheduler.
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

            # Basic ACP task
            acp_task = build_acp_task(
                node_id="node-basic",
                text="Write a hello world script",
                cwd="/tmp/basic-test",
                agent="test-acp-agent",
                execution_id="exec-basic",
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

            # Verify task completed and result returned to scheduler
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-basic"]
            assert len(task_result) == 1, (
                f"Should have result for basic test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"Task should complete successfully: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_quickstart_scenario_2_cross_protocol_workflow():
    """T037: Quickstart scenario 2 - Cross-protocol workflow (A2A + ACP).

    Verifies that workflows can mix A2A and ACP agents, with worker dispatching
    to correct protocol based on agent configuration.
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

            # Start A2A mock agent for cross-protocol workflow
            agent_proc = start_and_wait_for_a2a_agent(
                18765, Path(__file__).parent.parent.parent
            )
            processes.append(agent_proc)

            # Workflow with A2A task followed by ACP task
            a2a_task = build_canonical_task(
                node_id="node-cross-a2a",
                text="Plan the project",
                agent="mock-agent",
                execution_id="exec-cross-a2a",
            )

            acp_task = build_acp_task(
                node_id="node-cross-acp",
                text="Execute the plan",
                cwd="/tmp/cross-protocol-test",
                agent="test-acp-agent",
                execution_id="exec-cross-acp",
            )

            # Add both tasks
            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=a2a_task
            )
            assert response.status_code == 200

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(5)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify results for both tasks
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()

            # Verify A2A task completed
            a2a_result = [r for r in results if r["executionId"] == "exec-cross-a2a"]
            assert len(a2a_result) == 1, f"Should have result for A2A task: {results}"
            assert a2a_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"A2A task should complete successfully: {a2a_result[0]}"
            )

            # Verify ACP task completed
            acp_result = [r for r in results if r["executionId"] == "exec-cross-acp"]
            assert len(acp_result) == 1, f"Should have result for ACP task: {results}"
            assert acp_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"ACP task should complete successfully: {acp_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_quickstart_scenario_3_session_updates_in_history():
    """T038: Quickstart scenario 3 - Session update notifications in Task.history.

    Verifies that session/update notifications are converted to A2A Message objects
    and included in Task.history of the final result.
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

            # Task that generates session/update notifications
            acp_task = build_acp_task(
                node_id="node-updates",
                text="STREAM_CHUNKS",
                cwd="/tmp/updates-test",
                agent="test-acp-agent",
                execution_id="exec-updates",
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

            # Verify result includes history with session/update notifications
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-updates"]
            assert len(task_result) == 1, (
                f"Should have result for updates test: {results}"
            )

            # Verify task completed successfully
            assert task_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"Task should complete successfully: {task_result[0]}"
            )

            # Parse and verify Task.history contains MULTIPLE session/update entries per quickstart
            task_status = task_result[0]["taskStatus"]
            history = _extract_history(task_status)

            assert history and len(history) > 0, (
                f"Task.history should contain session/update notifications: {task_result[0]}"
            )
            # Verify MULTIPLE agent messages proving streaming behavior per quickstart scenario 3
            agent_entries = [h for h in history if h.get("role") == "agent"]
            assert len(agent_entries) >= 2, (
                f"Task.history should include MULTIPLE agent messages from STREAM_CHUNKS proving streaming per quickstart: got {len(agent_entries)} messages, expected >=2. History: {history}"
            )

        finally:
            cleanup_processes(processes)


def test_quickstart_scenario_5_timeout_and_cancellation():
    """T039: Quickstart scenario 5 - Timeout and graceful cancellation.

    Verifies that worker sends session/cancel when scheduler signals timeout, waits for
    graceful shutdown, and terminates subprocess within 10 seconds.
    Per FR-015 and FR-023: scheduler enforces timeout, worker waits indefinitely on session/prompt.
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

            # Task with 5-second delay that scheduler will timeout
            acp_task = build_acp_task(
                node_id="node-timeout",
                text="DELAY_5",
                cwd="/tmp/timeout-test",
                agent="test-acp-agent",
                execution_id="exec-timeout",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            # Wait for worker to pick up task and start session/prompt
            time.sleep(2)

            # Simulate scheduler detecting timeout - send cancel command
            cancel_command = {
                "executionId": "exec-timeout",
                "nodeId": "node-timeout",
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

            # Verify task result recorded as canceled by scheduler
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200, (
                f"Should get results: {result.status_code}"
            )
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-timeout"]
            assert len(task_result) == 1, (
                f"Should have result for timeout test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] == "canceled", (
                f"Task should be canceled by scheduler: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_quickstart_scenario_6_subprocess_crash_handling():
    """T040: Quickstart scenario 6 - Subprocess crash handling.

    Verifies that worker detects subprocess crashes and fails task with
    appropriate error message.
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

            # Task with EXIT_1 command to crash subprocess
            acp_task = build_acp_task(
                node_id="node-crash",
                text="EXIT_1",
                cwd="/tmp/crash-test",
                agent="test-acp-agent",
                execution_id="exec-crash",
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

            # Verify task failed due to subprocess crash
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-crash"]
            assert len(task_result) == 1, (
                f"Should have result for crash test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail when subprocess crashes: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_quickstart_scenario_7_malformed_jsonrpc():
    """T041: Quickstart scenario 7 - Malformed JSON-RPC response.

    Verifies that worker fails task when agent sends invalid JSON or malformed
    JSON-RPC structure.
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

            # Task with INVALID_JSONRPC command
            acp_task = build_acp_task(
                node_id="node-malformed",
                text="INVALID_JSONRPC",
                cwd="/tmp/malformed-test",
                agent="test-acp-agent",
                execution_id="exec-malformed",
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

            # Verify task failed due to malformed JSON-RPC
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [r for r in results if r["executionId"] == "exec-malformed"]
            assert len(task_result) == 1, (
                f"Should have result for malformed test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail on malformed JSON-RPC: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)
