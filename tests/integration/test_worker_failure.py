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

import os
import subprocess
import time
import signal
from pathlib import Path
from tests.testhelpers import cleanup_processes, cleanup_files, start_mock_orchestrator, wait_for_port
import pytest
import requests


def test_worker_handles_agent_process_failure():
    """Test worker handles agent process crashes gracefully."""
    # Simplified test for stdio-based worker that expects mock-agent failure

    mock_orchestrator_port = 19466
    worker_binary = "./bin/agentmaestro-worker"
    mock_agent_backup = "./bin/mock-agent.backup"
    processes = []

    try:
        # Temporarily move mock-agent binary to simulate failure
        import shutil
        if os.path.exists("./bin/mock-agent"):
            shutil.move("./bin/mock-agent", mock_agent_backup)

        # Start simple mock orchestrator
        orchestrator_proc = start_mock_orchestrator(mock_orchestrator_port, Path(__file__).parent.parent.parent)
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, "Mock orchestrator should start"

        # Add a task that will fail due to missing mock-agent binary
        import requests
        failure_task = {
            "id": "failure-task-789",
            "agent": "test-agent",  # Will fallback to missing mock-agent
            "request": {
                "input": "Task that will fail due to missing agent binary"
            }
        }

        # Add task to orchestrator
        response = requests.post(f"http://localhost:{mock_orchestrator_port}/add_task", json=failure_task)
        assert response.status_code == 200

        # Start worker
        worker_proc = subprocess.Popen(
            [worker_binary, "-orchestrator-url", f"http://localhost:{mock_orchestrator_port}", "-interval", "2s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        processes.append(worker_proc)

        # Wait for worker to attempt task and fail
        time.sleep(8)  # Give worker time to pick up task and fail

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Worker should have attempted to execute task and reported failure
        assert "failure-task-789" in worker_output, f"Worker should process the task: {worker_output}"

        # Worker should report execution failure
        failure_indicators = ["failed", "no such file", "executable file not found", "error"]
        has_failure = any(indicator in worker_output.lower() for indicator in failure_indicators)
        assert has_failure, f"Worker should report execution failure: {worker_output}"

        # Check that failure result was posted back
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) > 0, "Worker should post failure result back to orchestrator"
            # Check if result indicates failure
            result = results[0]
            assert "failure-task-789" in str(result), f"Result should contain task ID: {result}"

    finally:
        # Restore mock-agent binary
        if os.path.exists(mock_agent_backup):
            shutil.move(mock_agent_backup, "./bin/mock-agent")
        cleanup_processes(processes)


def test_worker_handles_malformed_task_data():
    """Test worker handles malformed or invalid task data."""
    # Test how worker handles tasks with missing required fields

    mock_orchestrator_port = 19468
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        orchestrator_proc = start_mock_orchestrator(mock_orchestrator_port, Path(__file__).parent.parent.parent)
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, "Mock orchestrator should start"

        import requests

        # Add malformed tasks that should cause processing issues
        malformed_tasks = [
            # Missing required fields
            {
                "id": "malformed-1"
                # Missing agent field, will cause unmarshaling errors
            },
            # Task with invalid structure for our worker
            {
                "not_an_id": "malformed-2",
                "random_field": True,
                "nested": {"invalid": "structure"}
            }
        ]

        # Add malformed tasks to orchestrator
        for task in malformed_tasks:
            response = requests.post(f"http://localhost:{mock_orchestrator_port}/add_task", json=task)
            assert response.status_code == 200

        # Start worker
        worker_proc = subprocess.Popen(
            [worker_binary, "-orchestrator-url", f"http://localhost:{mock_orchestrator_port}", "-interval", "2s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        processes.append(worker_proc)

        # Wait for worker to attempt processing malformed tasks
        time.sleep(10)  # Give worker time to pick up and process tasks

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Worker should have processed both malformed tasks gracefully (using fallbacks)
        assert "malformed-1" in worker_output, f"Worker should process malformed-1: {worker_output}"
        assert "completed successfully" in worker_output, f"Worker should complete malformed tasks using fallbacks: {worker_output}"

        # Verify that empty agent fields trigger the fallback mechanism
        fallback_lines = [line for line in worker_output.split('\n') if "Unknown agent type ''" in line]
        assert len(fallback_lines) >= 1, f"Worker should use fallback for empty agent: {worker_output}"

        # Check that results were posted back (showing resilient behavior)
        import requests
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 1, f"Worker should post results for malformed tasks: {results}"

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
        orchestrator_proc = start_mock_orchestrator(mock_orchestrator_port, Path(__file__).parent.parent.parent)
        processes.append(orchestrator_proc)

        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, "Mock orchestrator should start"

        import requests

        # Start worker
        worker_proc = subprocess.Popen(
            [worker_binary, "-orchestrator-url", f"http://localhost:{mock_orchestrator_port}", "-interval", "2s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        processes.append(worker_proc)

        # Let worker establish connection
        time.sleep(3)

        # Check initial poll count
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/poll_count")
        initial_poll_count = response.json()["count"]
        assert initial_poll_count > 0, "Worker should have polled initially"

        # Simulate orchestrator downtime
        requests.post(f"http://localhost:{mock_orchestrator_port}/simulate_downtime", json={"enabled": True})

        # Wait during downtime - worker should continue trying to poll
        time.sleep(8)  # 4 polling cycles during downtime

        # Restore orchestrator
        requests.post(f"http://localhost:{mock_orchestrator_port}/simulate_downtime", json={"enabled": False})

        # Wait for worker to resume normal polling
        time.sleep(6)

        # Check final poll count - should be higher despite downtime
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/poll_count")
        final_poll_count = response.json()["count"]
        assert final_poll_count > initial_poll_count, \
            f"Worker should have resumed polling: initial={initial_poll_count}, final={final_poll_count}"

        # Worker should still be running
        assert worker_proc.poll() is None, "Worker should survive orchestrator downtime"

    finally:
        cleanup_processes(processes)
