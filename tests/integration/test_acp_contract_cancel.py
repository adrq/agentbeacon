"""
ACP Protocol Contract Test - Session/Cancel Notification

This test verifies the worker's implementation of graceful cancellation via session/cancel.

Run with: uv run pytest tests/integration/test_acp_contract_cancel.py -v
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


def test_session_cancel_graceful_shutdown():
    """Contract test - session/cancel graceful shutdown.

    Verifies that worker handles scheduler cancel command and reports task as canceled.
    Worker responds to scheduler cancellation, does not self-timeout.
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

            # Use DELAY_5 special command - agent will work for 5 seconds
            acp_task = build_acp_task(
                node_id="node-cancel-graceful",
                text="DELAY_5",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-cancel-graceful",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            # Wait for worker to pick up task and start execution
            time.sleep(2)

            # Simulate scheduler timeout - send cancel command via worker sync protocol
            cancel_command = {
                "executionId": "exec-cancel-graceful",
                "nodeId": "node-cancel-graceful",
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

            # Verify TaskResult: scheduler recorded cancellation
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            cancel_result = [
                r for r in results if r["executionId"] == "exec-cancel-graceful"
            ]
            assert len(cancel_result) == 1, (
                f"Should have result for canceled task: {results}"
            )
            assert cancel_result[0]["taskStatus"]["state"] == "canceled", (
                f"Task should be marked as canceled: {cancel_result[0]}"
            )

        finally:
            cleanup_processes(processes)
