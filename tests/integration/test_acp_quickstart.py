"""T036-T041: ACP Quickstart Integration Tests

Verifies end-to-end scenarios from quickstart.md for ACP protocol support.

Run with: uv run pytest tests/integration/test_acp_quickstart.py -v
"""

import time
from pathlib import Path

import pytest
import requests

from tests.contracts.schema_helpers import build_acp_task, build_canonical_task
from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_and_wait_for_a2a_agent,
    start_mock_scheduler,
    wait_for_port,
)
from tests.integration.worker_test_helpers import (
    create_mock_scheduler,
    start_worker,
    clear_state,
    enqueue_session,
    get_agent_output,
    get_results,
    mark_complete,
    poll_until,
)


@pytest.fixture()
def mock_scheduler():
    url, port, proc, pm = create_mock_scheduler()
    yield url, port, proc
    cleanup_processes([proc])
    pm.release_port(port)


def test_quickstart_scenario_1_basic_acp_task(mock_scheduler):
    """T036: Quickstart scenario 1 - Basic ACP task execution.

    Verifies end-to-end flow: scheduler assigns ACP task -> worker spawns agent subprocess
    -> complete ACP protocol sequence -> task completes -> result returned to scheduler.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="Write a hello world script")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete successfully: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


@pytest.mark.skip(
    reason="Phase 5: A2A executor not yet implemented in new executor system"
)
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


def test_quickstart_scenario_3_session_updates_in_history(mock_scheduler):
    """T038: Quickstart scenario 3 - Session update notifications in output.

    Verifies that session/update notifications are accumulated and included in output.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="STREAM_CHUNKS")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete successfully: {results[0]}"
        )
        # Output arrives via mid-turn events (or sync result as fallback)
        output = get_agent_output(url)
        assert output is not None, (
            "Output should contain accumulated agent messages from events"
        )
        parts = output.get("parts", []) if isinstance(output, dict) else []
        assert len(parts) >= 2, (
            f"Output should contain multiple parts from session/update notifications: {output}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


@pytest.mark.skip(
    reason="Mock agent reads stdin sequentially inside _handle_prompt; "
    "session/cancel notification cannot be processed until DELAY_5 completes. "
    "Fix requires concurrent stdin reader in mock agent."
)
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


def test_quickstart_scenario_6_subprocess_crash_handling(mock_scheduler):
    """T040: Quickstart scenario 6 - Subprocess crash handling.

    Verifies that worker detects subprocess crashes and fails task with error.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="EXIT_1")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is not None, (
            f"Task should fail when subprocess crashes: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_quickstart_scenario_7_malformed_jsonrpc(mock_scheduler):
    """T041: Quickstart scenario 7 - Malformed JSON-RPC response.

    Verifies that worker fails task when agent sends invalid JSON.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="INVALID_JSONRPC")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is not None, (
            f"Task should fail on malformed JSON-RPC: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
