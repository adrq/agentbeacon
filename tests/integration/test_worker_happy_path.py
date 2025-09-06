"""
T005: Happy Path Test - Complete worker task execution cycle integration test.

This test verifies the complete worker lifecycle:
1. Worker polls and receives a task from orchestrator
2. Worker executes the task using mock-agent
3. Worker reports result back to orchestrator
4. Worker resumes polling for next task

Run with: uv run pytest tests/integration/test_worker_happy_path.py -v
"""

import subprocess
import time
from pathlib import Path
from tests.testhelpers import cleanup_processes, start_mock_orchestrator, wait_for_port


def test_worker_complete_task_execution_cycle():
    """Test complete cycle: sync idle → receive task → sync working → sync result → sync idle."""
    # Test the complete worker sync cycle using simple mock orchestrator

    mock_orchestrator_port = 19460
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator with sync endpoint
        orchestrator_proc = start_mock_orchestrator(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, (
            f"Mock orchestrator did not start on port {mock_orchestrator_port}"
        )

        import requests

        # Prepare a sample task for the worker
        sample_task = {
            "id": "task-123",
            "agent": "mock-agent",
            "executionId": "exec-123",
            "request": {"input": "Hello, please respond with a greeting"},
        }

        # Add task to orchestrator
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=sample_task
        )
        assert response.status_code == 200

        # Start worker pointing to mock orchestrator
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

        # Wait for complete sync execution cycle
        time.sleep(
            3
        )  # Give worker time to sync idle, receive task, execute, sync result

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify the task was processed via sync endpoint
        assert "task-123" in worker_output, (
            f"Worker should process task: {worker_output}"
        )
        assert "completed successfully" in worker_output, (
            f"Task should complete: {worker_output}"
        )

        # Verify sync endpoint usage (worker should use /api/worker/sync instead of /poll and /result)
        assert (
            "syncing with" in worker_output and "/api/worker/sync" in worker_output
        ), f"Worker should use sync endpoint: {worker_output}"

        # Verify worker receives task assignment and completes it
        assert "Received task assignment" in worker_output, (
            f"Worker should receive task via sync: {worker_output}"
        )

        # Verify sync protocol operation
        assert "Starting worker loop" in worker_output, (
            f"Worker should start sync loop: {worker_output}"
        )

        # Check that result was submitted via sync endpoint
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 1, f"Worker should submit result via sync: {results}"
            result = results[0]
            assert "task-123" in str(result), f"Result should contain task ID: {result}"
            # Result should be A2A-compliant from sync endpoint
            assert "taskStatus" in result or "nodeId" in result, (
                f"Result should be A2A format: {result}"
            )

        # Verify sync endpoint was called by checking orchestrator
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/sync_count")
        if response.status_code == 200:
            sync_data = response.json()
            assert sync_data["count"] > 0, (
                f"Orchestrator should receive sync calls: {sync_data}"
            )

    finally:
        cleanup_processes(processes)


def test_worker_handles_task_with_output():
    """Test worker properly captures and reports task output."""
    # Test that worker captures output from mock-agent and includes it in result

    mock_orchestrator_port = 19462
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        orchestrator_proc = start_mock_orchestrator(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, "Mock orchestrator should start successfully"

        import requests

        # Task that should produce output
        output_task = {
            "id": "output-task-456",
            "agent": "mock-agent",
            "request": {"input": "Generate a test response with some output"},
        }

        # Add task to orchestrator
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=output_task
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

        # Wait for task execution
        time.sleep(3)  # Give worker time to process task

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify task was processed
        assert "output-task-456" in worker_output, (
            f"Worker should process task: {worker_output}"
        )
        assert "completed successfully" in worker_output, (
            f"Task should complete: {worker_output}"
        )

        # Verify sync endpoint usage (worker should use /api/worker/sync instead of /poll and /result)
        assert (
            "syncing with" in worker_output and "/api/worker/sync" in worker_output
        ), f"Worker should use sync endpoint: {worker_output}"

        # Verify worker receives task assignment and completes it
        assert "Received task assignment" in worker_output, (
            f"Worker should receive task via sync: {worker_output}"
        )

        # Check that result was posted back with output
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 1, f"Worker should post result: {results}"
            result = results[0]
            assert "output-task-456" in str(result), (
                f"Result should contain task ID: {result}"
            )
            # Result should contain the mock-agent output
            assert "nodeId" in result and "taskStatus" in result, (
                f"Result should have required fields: {result}"
            )

    finally:
        cleanup_processes(processes)


def test_multiple_task_execution_sequence():
    """Test worker can handle multiple sequential tasks via sync endpoint."""
    # Test that worker processes multiple tasks in sequence using sync protocol

    mock_orchestrator_port = 19464
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator with sync endpoint support
        orchestrator_proc = start_mock_orchestrator(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, "Mock orchestrator should start"

        import requests

        # Prepare sequence of tasks with execution IDs
        tasks = [
            {
                "id": "seq-task-1",
                "agent": "mock-agent",
                "executionId": "exec-seq-1",
                "request": {"input": "First task in sequence"},
            },
            {
                "id": "seq-task-2",
                "agent": "mock-agent",
                "executionId": "exec-seq-2",
                "request": {"input": "Second task in sequence"},
            },
        ]

        # Add tasks to orchestrator
        for task in tasks:
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

        # Wait for both tasks to complete (worker processes them one by one via sync)
        time.sleep(5)  # Give worker time to sync and process both tasks

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify both tasks were processed via sync endpoint
        assert "seq-task-1" in worker_output, (
            f"Worker should process first task: {worker_output}"
        )
        assert "seq-task-2" in worker_output, (
            f"Worker should process second task: {worker_output}"
        )

        # Count successful completions
        completed_count = worker_output.count("completed successfully")
        assert completed_count >= 2, (
            f"Expected 2 completed tasks, got {completed_count}: {worker_output}"
        )

        # Verify sync endpoint usage for multiple tasks
        assert (
            "syncing with" in worker_output and "/api/worker/sync" in worker_output
        ), f"Worker should use sync endpoint for multiple tasks: {worker_output}"

        # Verify both tasks were received and executed via sync endpoint
        task_assignment_count = worker_output.count("Received task assignment")
        assert task_assignment_count >= 2, (
            f"Worker should receive 2 task assignments: {worker_output}"
        )

        # Check that results were submitted via sync endpoint
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 2, (
                f"Worker should submit results for both tasks via sync: {results}"
            )

            # Check that both task IDs are present in A2A-format results
            results_str = str(results)
            assert "seq-task-1" in results_str, (
                f"Results should contain first task: {results}"
            )
            assert "seq-task-2" in results_str, (
                f"Results should contain second task: {results}"
            )

            # Verify A2A-compliant format for multiple results
            for result in results:
                assert "taskStatus" in result or "nodeId" in result, (
                    f"Each result should be A2A format: {result}"
                )

        # Verify multiple sync calls were made
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/sync_count")
        if response.status_code == 200:
            sync_data = response.json()
            assert sync_data["count"] >= 4, (
                f"Should have multiple sync calls for 2 tasks (idle+working+result per task): {sync_data}"
            )

    finally:
        cleanup_processes(processes)


def test_worker_uses_agents_yaml_config():
    """Test worker correctly looks up agent configurations from agents.yaml file."""
    # Test that worker reads agent config from examples/agents.yaml and uses configured command/args

    mock_orchestrator_port = 19465
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        orchestrator_proc = start_mock_orchestrator(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orch_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert orch_ready, "Mock orchestrator should start successfully"

        import requests

        # Task that specifies test-config-agent from examples/agents.yaml
        config_task = {
            "id": "config-test-task-789",
            "agent": "test-config-agent",  # This agent is in examples/agents.yaml with special args
            "request": {"prompt": "Test task using configured agent"},
        }

        # Add task to orchestrator
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=config_task
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

        # Wait for task execution
        time.sleep(3)  # Give worker time to process task

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify task was processed using config
        assert "config-test-task-789" in worker_output, (
            f"Worker should process task: {worker_output}"
        )
        assert "test-config-agent" in worker_output, (
            f"Worker should reference configured agent: {worker_output}"
        )

        # Check if CONFIG_LOADED appears in task results rather than worker logs
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        result_contains_config = False
        if response.status_code == 200:
            results = response.json()
            if results:
                result_contains_config = "CONFIG_LOADED" in str(results[0])

        assert "CONFIG_LOADED" in worker_output or result_contains_config, (
            f"Worker should use configured command args. Worker output: {worker_output}. Results: {response.json() if response.status_code == 200 else 'No results'}"
        )
        assert "completed successfully" in worker_output, (
            f"Task should complete: {worker_output}"
        )

        # Verify sync endpoint usage (worker should use /api/worker/sync instead of /poll and /result)
        assert (
            "syncing with" in worker_output and "/api/worker/sync" in worker_output
        ), f"Worker should use sync endpoint: {worker_output}"

        # Verify worker receives task assignment via sync
        assert "Received task assignment" in worker_output, (
            f"Worker should receive task via sync: {worker_output}"
        )

        # Check that result was posted back
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 1, f"Worker should post result: {results}"
            result = results[0]
            assert "config-test-task-789" in str(result), (
                f"Result should contain task ID: {result}"
            )

    finally:
        cleanup_processes(processes)
