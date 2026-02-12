"""
ACP Protocol Contract Test - Error Handling

This test verifies the worker's handling of malformed JSON-RPC responses.

Run with: uv run pytest tests/integration/test_acp_contract_errors.py -v
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


def test_malformed_jsonrpc_response():
    """Contract test - malformed JSON-RPC response.

    Verifies that worker fails task when agent sends invalid JSON or malformed JSON-RPC structure.
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

            # Use INVALID_JSONRPC special command to trigger malformed response
            acp_task = build_acp_task(
                node_id="node-malformed",
                text="INVALID_JSONRPC",
                cwd="/tmp/test-workdir",
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
