"""
E2E Test: Complete orchestrator workflow execution with proper A2A protocol.

This test verifies the complete system integration:
1. Orchestrator starts scheduler and workers
2. Workflow registration via REST API
3. Workflow execution via proper A2A JSON-RPC protocol
4. Task completion monitoring through A2A endpoints
5. Graceful shutdown of all processes

Run with: uv run pytest tests/e2e/test_full_orchestrator_workflow.py -v
"""

import re
import signal
import subprocess
import time
from pathlib import Path

import pytest
import requests
from tests.testhelpers import (
    PortManager,
    TempDatabase,
    cleanup_processes,
    wait_for_port,
    start_orchestrator,
    register_workflow,
    submit_workflow_via_a2a,
    poll_a2a_task_status,
    count_processes_by_name,
    get_current_test_name,
    parse_agent_log,
)


def test_full_orchestrator_workflow_execution():
    """Test complete orchestrator workflow execution with A2A protocol."""
    port_manager = PortManager()
    test_port = port_manager.allocate_port()
    processes = []

    # Clear any existing log file for this test before starting
    test_name = get_current_test_name("test_full_orchestrator_workflow_execution")
    log_file = Path(f"logs/{test_name}.log")
    if log_file.exists():
        log_file.unlink()

    try:
        # Create temporary database
        with TempDatabase() as db_url:
            # Start orchestrator with 2 workers using the complete database URL
            # This avoids URL reconstruction issues in start_orchestrator
            orchestrator_proc = start_orchestrator(test_port, workers=2, db_url=db_url)
            processes.append(orchestrator_proc)

            # Wait for system to be ready
            print(f"DEBUG: Waiting for orchestrator on port {test_port}")
            system_ready = wait_for_port(test_port, timeout=15)
            assert system_ready, (
                f"Orchestrator system did not start on port {test_port} within 15 seconds"
            )
            print(f"DEBUG: Orchestrator ready on port {test_port}")

            # Verify health endpoint responds properly
            response = requests.get(
                f"http://localhost:{test_port}/api/health", timeout=5
            )
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

            # Verify processes are running
            time.sleep(2)  # Let workers start
            scheduler_count = count_processes_by_name("agentmaestro-scheduler")
            worker_count = count_processes_by_name("agentmaestro-worker")

            assert scheduler_count == 1, (
                f"Expected exactly 1 scheduler process, found {scheduler_count}"
            )
            assert worker_count == 2, (
                f"Expected exactly 2 worker processes, found {worker_count}"
            )

            # Register a 2-node sequential workflow with bracketed format prompts
            workflow_yaml = """
name: e2e-full-test
namespace: integration
description: Full orchestrator E2E test workflow
nodes:
  - id: task-1
    agent: mock-agent
    request:
      prompt: "[e2e-execution][task-1] NOW Process data step 1"
  - id: task-2
    agent: mock-agent
    depends_on: [task-1]
    request:
      prompt: "[e2e-execution][task-2] NOW Process data step 2"
""".strip()

            scheduler_url = f"http://localhost:{test_port}"
            workflow_ref = register_workflow(scheduler_url, workflow_yaml)
            assert workflow_ref.startswith("integration/e2e-full-test:"), (
                f"Workflow ref should start with 'integration/e2e-full-test:', got: {workflow_ref}"
            )

            # Extract and validate the UUID part
            ref_parts = workflow_ref.split(":")
            assert len(ref_parts) == 2, (
                f"Workflow ref should have format 'namespace/name:uuid', got: {workflow_ref}"
            )
            namespace_name, version_uuid = ref_parts
            assert namespace_name == "integration/e2e-full-test", (
                f"Expected 'integration/e2e-full-test', got: {namespace_name}"
            )

            # Validate UUID format (basic check)
            uuid_pattern = (
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            )
            assert re.match(uuid_pattern, version_uuid), (
                f"Version should be a valid UUID, got: {version_uuid}"
            )

            # Submit workflow execution via A2A protocol
            task_result = submit_workflow_via_a2a(scheduler_url, workflow_ref)
            assert "id" in task_result, f"Task result should have ID: {task_result}"

            task_id = task_result["id"]

            # Poll task status until completion
            final_task = poll_a2a_task_status(scheduler_url, task_id, timeout=120)

            # Verify task completed successfully
            assert final_task["status"]["state"] == "completed", (
                f"Task should complete successfully: {final_task}"
            )
            assert "artifacts" in final_task, (
                f"Completed task should have artifacts: {final_task}"
            )
            assert len(final_task["artifacts"]) >= 1, (
                f"Task should have execution status artifact: {final_task}"
            )

            # Verify execution status artifact exists
            execution_artifact = final_task["artifacts"][0]
            assert execution_artifact["artifactId"] == "execution-status", (
                f"Should have execution status artifact: {execution_artifact}"
            )
            assert "completed" in execution_artifact["parts"][0]["text"], (
                f"Artifact should show completed status: {execution_artifact}"
            )

            # Verify scheduler still responding
            response = requests.get(
                f"http://localhost:{test_port}/api/health", timeout=5
            )
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

            # Verify mock agent logging functionality
            log_entries = parse_agent_log(test_name)
            assert len(log_entries) == 2, (
                f"Expected exactly 2 log entries for 2-node workflow, got {len(log_entries)}: {log_entries}"
            )

            # Verify first task (task-1) log entry with bracketed format
            first_entry = log_entries[0]
            assert first_entry.get("execution_id") == "e2e-execution", (
                f"First entry should have execution_id 'e2e-execution', got: {first_entry.get('execution_id')}"
            )
            assert first_entry.get("node_id") == "task-1", (
                f"First entry should have node_id 'task-1', got: {first_entry.get('node_id')}"
            )
            assert "Process data step 1" in first_entry.get("task_text", ""), (
                f"First entry should contain 'Process data step 1': {first_entry.get('task_text')}"
            )

            # Verify second task (task-2) log entry with bracketed format
            second_entry = log_entries[1]
            assert second_entry.get("execution_id") == "e2e-execution", (
                f"Second entry should have execution_id 'e2e-execution', got: {second_entry.get('execution_id')}"
            )
            assert second_entry.get("node_id") == "task-2", (
                f"Second entry should have node_id 'task-2', got: {second_entry.get('node_id')}"
            )
            assert "Process data step 2" in second_entry.get("task_text", ""), (
                f"Second entry should contain 'Process data step 2': {second_entry.get('task_text')}"
            )

            # Verify timestamps are properly set (not "NOW")
            for i, entry in enumerate(log_entries):
                timestamp = entry.get("timestamp", "")
                assert timestamp != "NOW", (
                    f"Entry {i} timestamp should be replaced, not 'NOW': {timestamp}"
                )
                assert len(timestamp) == 20, (
                    f"Entry {i} timestamp should be 20 characters ISO format: {timestamp}"
                )

            # Test graceful shutdown
            initial_scheduler_count = count_processes_by_name("agentmaestro-scheduler")
            initial_worker_count = count_processes_by_name("agentmaestro-worker")

            assert initial_scheduler_count == 1, (
                f"Expected exactly 1 scheduler process before shutdown, found {initial_scheduler_count}"
            )
            assert initial_worker_count == 2, (
                f"Expected exactly 2 worker processes before shutdown, found {initial_worker_count}"
            )

            # Send SIGTERM for graceful shutdown
            orchestrator_proc.send_signal(signal.SIGTERM)

            # Wait for orchestrator to exit
            try:
                orchestrator_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pytest.fail("Orchestrator did not exit within 15 seconds after SIGTERM")

            # Wait for children cleanup
            time.sleep(3)

            # Verify no orphaned processes remain
            final_scheduler_count = count_processes_by_name("agentmaestro-scheduler")
            final_worker_count = count_processes_by_name("agentmaestro-worker")

            assert final_scheduler_count == 0, (
                f"Found {final_scheduler_count} orphaned scheduler processes"
            )
            assert final_worker_count == 0, (
                f"Found {final_worker_count} orphaned worker processes"
            )

    finally:
        # Clean up all processes and release port
        cleanup_processes(processes)
        port_manager.release_port(test_port)
