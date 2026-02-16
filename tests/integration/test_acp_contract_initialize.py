"""ACP Protocol Contract Tests - Initialize Method

Verifies the worker's implementation of the ACP initialize protocol method.

Run with: uv run pytest tests/integration/test_acp_contract_initialize.py -v
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


def test_initialize_success(mock_scheduler):
    """Contract test - initialize success with protocol_version=1.

    Verifies that worker sends initialize request with protocolVersion=1 and
    agent completes successfully through the full ACP protocol sequence.
    """
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="Test initialize protocol")
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


@pytest.mark.skip(reason="Requires mock agent PROTOCOL_V2 special command")
def test_initialize_version_mismatch():
    """Contract test - initialize version mismatch (protocol_version=2).

    Verifies that worker fails task when agent returns unsupported protocol version.
    """
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            # Start mock orchestrator
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # Create ACP task that targets agent configured for protocol_version=2
            acp_task = build_acp_task(
                node_id="node-version-test",
                text="Trigger version mismatch",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent-version2",
                execution_id="exec-version-test",
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

            # Verify task failed due to version mismatch
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [
                r for r in results if r["executionId"] == "exec-version-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for version test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail on version mismatch: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


@pytest.mark.skip(reason="Requires mock agent HANG_INITIALIZE special command")
def test_initialize_timeout():
    """Contract test - initialize timeout.

    Verifies that worker fails task when agent doesn't respond to initialize within timeout.
    Uses test-acp-agent-timeout which hangs during initialize and has 2s timeout.
    """
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            # Start mock orchestrator
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # Create ACP task with test-acp-agent-timeout (hangs during initialize with 2s timeout)
            acp_task = build_acp_task(
                node_id="node-timeout-test",
                text="Test initialize timeout",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent-timeout",
                execution_id="exec-timeout-test",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            # Wait long enough for 2s initialize timeout to occur
            time.sleep(4)

            worker_proc.terminate()
            worker_proc.communicate(timeout=5)

            # Verify task failed due to timeout
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [
                r for r in results if r["executionId"] == "exec-timeout-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for timeout test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["failed", "error"], (
                f"Task should fail on timeout: {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)
