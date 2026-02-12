"""
ACP Protocol Contract Test - Session/Request_Permission Auto-Approval

This test verifies the worker's auto-approval of permission requests.

Run with: uv run pytest tests/integration/test_acp_contract_permissions.py -v
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


def test_session_request_permission_auto_approval():
    """Contract test - session/request_permission auto-approval.

    Verifies that worker responds to session/request_permission with approval outcome
    selecting first allow_once/allow_always option, and logs warning at WARN level
    with message containing 'Auto-approved session/request_permission' and tool ID.
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

            # Task that will trigger session/request_permission during execution
            acp_task = build_acp_task(
                node_id="node-permission-test",
                text="REQUEST_PERMISSION",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-permission-test",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200

            worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
            processes.append(worker_proc)

            time.sleep(3)

            worker_proc.terminate()
            # Note: start_worker() uses stderr=subprocess.STDOUT, so communicate() returns (stdout, None)
            # where stdout already contains both stdout and stderr merged together
            worker_output, _ = worker_proc.communicate(timeout=5)

            # Verify task completed successfully after auto-approval
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            task_result = [
                r for r in results if r["executionId"] == "exec-permission-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for permission test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] in ["completed", "success"], (
                f"Task should complete after auto-approval: {task_result[0]}"
            )

            # Verify WARN log contains auto-approval message WITH tool ID
            assert "WARN" in worker_output or "warn" in worker_output.lower(), (
                f"Worker should log at WARN level: {worker_output}"
            )
            # Must contain both auto-approval message AND tool identifier
            has_auto_approval = (
                "Auto-approved session/request_permission" in worker_output
                or "auto-approved" in worker_output.lower()
            )
            # Look for explicit tool identifier beyond the method name
            # Examples: "tool_id=test-tool-123" or "toolCallId: test-tool-123" or "toolCallId=test-tool-123"
            has_tool_id = (
                "toolId" in worker_output
                or "tool_id" in worker_output
                or "toolCallId" in worker_output
            )
            assert has_auto_approval and has_tool_id, (
                f"Worker should log auto-approval WITH explicit tool ID (not just 'permission' from method name): {worker_output}"
            )

        finally:
            cleanup_processes(processes)
