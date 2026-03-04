"""
E2E Test: Complete orchestrator workflow execution with proper A2A protocol.

This test verifies the complete system integration:
1. Orchestrator starts scheduler and workers
2. Workflow registration via REST API
3. Workflow execution via proper A2A JSON-RPC protocol
4. Task completion monitoring through A2A endpoints
5. Graceful shutdown of all processes

Uses PID-based process tracking to avoid conflicts with separately running instances.

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
    orchestrator_context,
    start_and_wait_for_a2a_agent,
    register_workflow,
    submit_workflow_via_a2a,
    poll_a2a_task_status,
    get_current_test_name,
    parse_agent_log,
    cleanup_processes,
)

pytestmark = pytest.mark.skip(reason="Deferred: DAG model removed")


def test_full_orchestrator_workflow_execution():
    """Test complete orchestrator workflow execution with A2A protocol using PID tracking."""
    test_name = get_current_test_name("test_full_orchestrator_workflow_execution")

    # Clear any existing log file for this test before starting
    log_file = Path(f"logs/{test_name}.log")
    if log_file.exists():
        log_file.unlink()

    # Use fixed agent port to match agents.yaml configuration
    # The orchestrator uses dynamic ports (19456+), so this won't conflict
    agent_port = 18765
    agent_proc = None

    try:
        # Start mock-agent A2A server on port matching agents.yaml
        agent_proc, _ = start_and_wait_for_a2a_agent(
            agent_port, Path(__file__).parent.parent.parent
        )

        # Use orchestrator_context for complete lifecycle management
        with orchestrator_context(workers=2, test_name=test_name) as orch:
            scheduler_url = orch["url"]
            tracker = orch["tracker"]

            print(f"DEBUG: Orchestrator ready on port {orch['port']}")

            # Verify health endpoint responds properly
            response = requests.get(f"{scheduler_url}/api/health", timeout=5)
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

            # Verify processes using PID tracking (not global scanning)
            tracker.assert_exact_count("scheduler", 1)
            tracker.assert_exact_count("worker", 2)

            # Register a 2-node sequential workflow with bracketed format prompts
            import uuid

            msg_id_1 = str(uuid.uuid4())
            msg_id_2 = str(uuid.uuid4())

            workflow_yaml = f"""
name: e2e-full-test
description: Full orchestrator E2E test workflow
tasks:
  - id: task-1
    agent: mock-agent
    task:
      message:
        kind: message
        messageId: "{msg_id_1}"
        role: user
        parts:
          - kind: text
            text: "[e2e-execution][task-1] NOW Process data step 1"
  - id: task-2
    agent: mock-agent
    depends_on: [task-1]
    task:
      message:
        kind: message
        messageId: "{msg_id_2}"
        role: user
        parts:
          - kind: text
            text: "[e2e-execution][task-2] NOW Process data step 2"
""".strip()

            workflow_ref = register_workflow(
                scheduler_url, workflow_yaml, namespace="integration"
            )
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

            # Verify task completed successfully (new format: status is string)
            assert final_task["status"] == "completed", (
                f"Task should complete successfully: {final_task}"
            )
            assert "taskStates" in final_task or "task_states" in final_task, (
                f"Completed task should have task states: {final_task}"
            )

            # Verify scheduler still responding
            response = requests.get(f"{scheduler_url}/api/health", timeout=5)
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

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

            # Test graceful shutdown using PID tracking
            # Verify processes are still alive before shutdown
            tracker.assert_exact_count("scheduler", 1)
            tracker.assert_exact_count("worker", 2)

            # Send SIGTERM for graceful shutdown
            orch["orchestrator"].send_signal(signal.SIGTERM)

            # Wait for orchestrator to exit
            try:
                orch["orchestrator"].wait(timeout=15)
            except subprocess.TimeoutExpired:
                pytest.fail("Orchestrator did not exit within 15 seconds after SIGTERM")

            # Wait for children cleanup
            time.sleep(3)

            # Verify no tracked processes remain alive (PID-based check)
            final_scheduler_count = tracker.count_alive("scheduler")
            final_worker_count = tracker.count_alive("worker")

            assert final_scheduler_count == 0, (
                f"Found {final_scheduler_count} tracked scheduler PIDs still alive after shutdown"
            )
            assert final_worker_count == 0, (
                f"Found {final_worker_count} tracked worker PIDs still alive after shutdown"
            )

    finally:
        # Clean up agent process
        if agent_proc:
            cleanup_processes([agent_proc])
