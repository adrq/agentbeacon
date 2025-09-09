"""
T006: Failure Handling Test - Worker failure scenarios integration test.

This test verifies that workers properly handle various failure scenarios:
1. Agent process failures and crashes
2. Network connectivity issues
3. Malformed task data
4. Task execution timeouts

Workers should report failures properly and continue polling after failures.

Run with: uv run pytest tests/integration/test_worker_failure.py -v
"""

import subprocess
import time
from pathlib import Path

import requests

from tests.testhelpers import cleanup_processes, start_mock_scheduler, wait_for_port
from tests.contracts.schema_helpers import (
    DEFAULT_WORKFLOW_REF,
    DEFAULT_WORKFLOW_REGISTRY_ID,
    build_canonical_task,
)


def test_worker_handles_agent_process_failure():
    """Test worker handles agent process crashes gracefully."""
    # Test for stdio-based worker that expects Python mock-agent failure

    mock_orchestrator_port = 19466
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Temporarily break Python environment to simulate agent failure
        # We'll use a non-existent agent name to trigger the fallback error path

        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock scheduler should start"

        # Add a task that will fail due to agent execution error
        failure_task = build_canonical_task(
            node_id="failure-task-789",
            agent="nonexistent-agent",
            text="Task that will fail due to nonexistent agent",
        )

        # Add task to orchestrator
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=failure_task
        )
        assert response.status_code == 200

        # Start worker
        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "-orchestrator-url",
                f"http://localhost:{mock_orchestrator_port}",
                "-interval",
                "1s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        # Wait for worker to attempt task, fail, and sync result back
        time.sleep(3)  # Give worker time to pick up task, fail, and post result

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Worker should have attempted to execute task and reported failure
        assert "failure-task-789" in worker_output, (
            f"Worker should process the task: {worker_output}"
        )

        # Worker should report nonexistent-agent not found
        assert "nonexistent-agent" in worker_output, (
            f"Worker should process with nonexistent-agent: {worker_output}"
        )
        assert "not found" in worker_output, (
            f"Worker should report agent not found: {worker_output}"
        )
        assert "failed" in worker_output, (
            f"Worker should report task failed: {worker_output}"
        )

        # Check that failure result was posted back
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) > 0, (
                "Worker should post failure result back to orchestrator"
            )
            # Check if result indicates failure
            result = results[0]
            assert "failure-task-789" in str(result), (
                f"Result should contain task ID: {result}"
            )

    finally:
        cleanup_processes(processes)


def test_worker_handles_malformed_task_data():
    """Test worker handles malformed or invalid task data."""
    # Test how worker properly fails tasks with missing required fields and provides helpful errors

    mock_orchestrator_port = 19468
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock scheduler should start"

        # Add malformed tasks that should cause processing issues
        malformed_tasks = [
            build_canonical_task(
                node_id="malformed-1",
                agent="",
                task_body={"messages": []},
                validate_task=False,
            ),
            build_canonical_task(
                node_id="malformed-2",
                task_body={
                    "messages": [
                        {
                            "messageId": "malformed-2-msg",
                            "kind": "message",
                            "role": "user",
                            # Intentionally omit parts to trigger validation failure downstream
                        }
                    ]
                },
                validate_task=False,
            ),
        ]

        # Add malformed tasks to orchestrator
        for task in malformed_tasks:
            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=task
            )
            assert response.status_code == 200

        # Start worker
        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "-orchestrator-url",
                f"http://localhost:{mock_orchestrator_port}",
                "-interval",
                "1s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        # Wait for worker to attempt processing malformed tasks and sync results
        time.sleep(4)  # Give worker time to pick up tasks, fail, and post results

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Worker should have processed malformed-1 (with empty agent field)
        assert "malformed-1" in worker_output, (
            f"Worker should process malformed-1: {worker_output}"
        )

        # Should see empty agent processing and failure (no default assignment now)
        assert "agent ''" in worker_output or "agent '' not found" in worker_output, (
            f"Worker should process with empty agent and report error: {worker_output}"
        )
        assert "failed" in worker_output.lower(), (
            f"Worker should report task failed: {worker_output}"
        )

        # Check that results were posted back (showing resilient behavior)
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 1, (
                f"Worker should post results for malformed tasks: {results}"
            )

    finally:
        cleanup_processes(processes)


def test_worker_surfaces_adapter_rejection():
    """Test worker surfaces adapter rejection errors for canonical payloads."""
    # Use FAIL_NODE command to force the mock-agent stdio adapter to reject the task payload

    mock_orchestrator_port = 19472
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock scheduler should start"

        rejection_task = build_canonical_task(
            node_id="adapter-reject-task",
            text="FAIL_NODE",
            protocol_metadata={"trigger": "adapter-rejection"},
        )

        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=rejection_task
        )
        assert response.status_code == 200

        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "-orchestrator-url",
                f"http://localhost:{mock_orchestrator_port}",
                "-interval",
                "1s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        time.sleep(3)

        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        assert "adapter-reject-task" in worker_output, (
            f"Worker should attempt the adapter-reject-task: {worker_output}"
        )
        assert "FAIL_NODE" in worker_output, (
            f"FAIL_NODE command should surface in worker logs: {worker_output}"
        )
        assert "failed" in worker_output.lower(), (
            f"Worker logs should record the failure: {worker_output}"
        )
        assert DEFAULT_WORKFLOW_REGISTRY_ID in worker_output, (
            f"Workflow registry id should remain visible even on failure: {worker_output}"
        )
        assert DEFAULT_WORKFLOW_REF in worker_output, (
            f"Workflow ref should remain visible even on failure: {worker_output}"
        )

        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        results = response.json()
        assert results, (
            "Worker should report the adapter failure back to the orchestrator"
        )
        result = results[0]
        assert result["nodeId"] == "adapter-reject-task", (
            f"Result should correspond to adapter-reject-task: {result}"
        )
        status = result.get("taskStatus", {})
        assert status.get("state") == "failed", (
            f"Adapter rejection should mark task as failed: {status}"
        )
        failure_message = status.get("message") or {}
        assert failure_message.get("role") == "assistant", (
            f"Failure message role should be assistant: {failure_message}"
        )
        parts = failure_message.get("parts") or []
        assert parts, (
            f"Failure message should include parts describing the error: {failure_message}"
        )
        first_part = parts[0]
        assert first_part.get("kind") == "text", (
            f"Failure part should be textual: {first_part}"
        )
        failure_text = first_part.get("text", "")
        assert "Mock agent failure" in failure_text and "FAIL_NODE" in failure_text, (
            f"Failure text should mention Mock agent failure and FAIL_NODE: {failure_text}"
        )
        assert not result.get("artifacts"), (
            f"Adapter rejection should not produce artifacts: {result}"
        )

    finally:
        cleanup_processes(processes)


def test_worker_handles_orchestrator_connection_loss():
    """Test worker handles temporary loss of orchestrator connection."""
    # Test that worker gracefully handles orchestrator downtime and recovers

    mock_orchestrator_port = 19471
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock scheduler should start"

        import requests

        # Start worker
        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "-orchestrator-url",
                f"http://localhost:{mock_orchestrator_port}",
                "-interval",
                "1s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        # Let worker establish connection
        time.sleep(2)

        # Check initial sync count
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/sync_count")
        initial_sync_count = response.json()["count"]
        assert initial_sync_count > 0, "Worker should have synced initially"

        # Simulate orchestrator downtime
        requests.post(
            f"http://localhost:{mock_orchestrator_port}/simulate_downtime",
            json={"enabled": True},
        )

        # Wait during downtime - worker should continue trying to poll
        time.sleep(4)  # 4 polling cycles during downtime

        # Restore orchestrator
        requests.post(
            f"http://localhost:{mock_orchestrator_port}/simulate_downtime",
            json={"enabled": False},
        )

        # Wait for worker to resume normal polling
        time.sleep(3)

        # Check final sync count - should be higher despite downtime
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/sync_count")
        final_sync_count = response.json()["count"]
        assert final_sync_count > initial_sync_count, (
            f"Worker should have resumed syncing: initial={initial_sync_count}, final={final_sync_count}"
        )

        # Worker should still be running
        assert worker_proc.poll() is None, "Worker should survive orchestrator downtime"

    finally:
        cleanup_processes(processes)


def test_worker_fails_on_unknown_agent():
    """Test worker properly fails when given an unknown agent name."""
    # Test that worker rejects unknown agents and provides helpful error message

    mock_orchestrator_port = 19470
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock scheduler should start"

        import requests

        # Task with an agent that doesn't exist in examples/agents.yaml
        unknown_agent_task = {
            "id": "unknown-agent-task-999",
            "agent": "nonexistent-agent-xyz",  # This agent is not in examples/agents.yaml
            "request": {"input": "Task that should fail due to unknown agent"},
        }

        # Add task to orchestrator
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task",
            json=unknown_agent_task,
        )
        assert response.status_code == 200

        # Start worker (uses hardcoded examples/agents.yaml)
        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "-orchestrator-url",
                f"http://localhost:{mock_orchestrator_port}",
                "-interval",
                "1s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        # Wait for worker to attempt task, fail, and sync result
        time.sleep(3)  # Give worker time to pick up task, fail, and post result

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Worker should have attempted to execute task
        assert "unknown-agent-task-999" in worker_output, (
            f"Worker should process the task: {worker_output}"
        )
        assert "nonexistent-agent-xyz" in worker_output, (
            f"Worker should reference unknown agent: {worker_output}"
        )

        # This will FAIL initially - worker should reject unknown agents but currently uses fallback
        assert "not found" in worker_output.lower(), (
            f"Worker should report agent not found: {worker_output}"
        )
        assert "available agents" in worker_output.lower(), (
            f"Worker should list available agents: {worker_output}"
        )
        assert "mock-agent" in worker_output, (
            f"Worker should list mock-agent as available: {worker_output}"
        )

        # Check that failure result was posted back
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) > 0, (
                "Worker should post failure result back to orchestrator"
            )
            result = results[0]
            assert "unknown-agent-task-999" in str(result), (
                f"Result should contain task ID: {result}"
            )
            # Result should indicate failure
            assert "failed" in str(result).lower() or "error" in str(result).lower(), (
                f"Result should indicate failure: {result}"
            )

    finally:
        cleanup_processes(processes)
