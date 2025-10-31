"""Integration tests for ACP session/update variants.

Tests that worker correctly handles all SessionUpdate variants from the ACP spec,
especially those without a content field (plan, tool_call, tool_call_update,
available_commands_update, current_mode_update).

These variants were causing deserialization failures before the enum fix.
"""

import time
from pathlib import Path

import requests

from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_mock_scheduler,
    start_worker,
    wait_for_port,
)
from tests.contracts.schema_helpers import build_acp_task


def test_session_update_plan_variant():
    """Test worker handles session/update with plan variant (no content field)."""
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
                node_id="node-plan-test",
                text="SEND_PLAN",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-plan-test",
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

            # Verify task completed without deserialization error
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()

            task_result = [r for r in results if r["executionId"] == "exec-plan-test"]
            assert len(task_result) == 1, f"Should have result for plan test: {results}"
            assert task_result[0]["taskStatus"]["state"] == "completed", (
                f"Task should complete (no deserialization error): {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_session_update_tool_call_variant():
    """Test worker handles session/update with tool_call variant (minimal content)."""
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
                node_id="node-tool-call-test",
                text="SEND_TOOL_CALL",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-tool-call-test",
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

            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()

            task_result = [
                r for r in results if r["executionId"] == "exec-tool-call-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for tool_call test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] == "completed", (
                f"Task should complete (no deserialization error): {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_session_update_mode_update_variant():
    """Test worker handles session/update with current_mode_update variant."""
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
                node_id="node-mode-test",
                text="SEND_MODE_UPDATE",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-mode-test",
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

            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()

            task_result = [r for r in results if r["executionId"] == "exec-mode-test"]
            assert len(task_result) == 1, (
                f"Should have result for mode update test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] == "completed", (
                f"Task should complete (no deserialization error): {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)


def test_session_update_commands_update_variant():
    """Test worker handles session/update with available_commands_update variant."""
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
                node_id="node-commands-test",
                text="SEND_COMMANDS_UPDATE",
                cwd="/tmp/test-workdir",
                agent="test-acp-agent",
                execution_id="exec-commands-test",
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

            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()

            task_result = [
                r for r in results if r["executionId"] == "exec-commands-test"
            ]
            assert len(task_result) == 1, (
                f"Should have result for commands update test: {results}"
            )
            assert task_result[0]["taskStatus"]["state"] == "completed", (
                f"Task should complete (no deserialization error): {task_result[0]}"
            )

        finally:
            cleanup_processes(processes)
