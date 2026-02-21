"""ACP Protocol Contract Tests - Session/Prompt Method

Verifies the worker's implementation of session/prompt and response handling.

Run with: uv run pytest tests/integration/test_acp_contract_prompt.py -v
"""

import time
from pathlib import Path

import pytest
import requests

from tests.contracts.schema_helpers import build_acp_task
from tests.testhelpers import (
    PortManager,
    cleanup_processes,
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


def test_session_prompt_end_turn_success(mock_scheduler):
    """Contract test - session/prompt end_turn success.

    Verifies that worker sends session/prompt, receives stopReason="end_turn",
    and reports success.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="Complete this task successfully")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete successfully with end_turn: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_prompt_with_session_update_notifications(mock_scheduler):
    """Contract test - session/prompt with session/update notifications.

    Verifies that worker receives and accumulates session/update notifications during
    session/prompt execution and includes them in output.
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
            f"Task should complete with session/update notifications: {results[0]}"
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


def test_session_prompt_error(mock_scheduler):
    """Contract test - session/prompt subprocess crash.

    Verifies that worker detects subprocess crash (FAIL_NODE sends SIGKILL)
    and reports error. Note: this tests the "subprocess closed before response"
    path, not the stopReason="error" JSON-RPC path.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="FAIL_NODE")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is not None, (
            f"Task should fail when stopReason is error: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
