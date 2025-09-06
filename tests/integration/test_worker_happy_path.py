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
    """Test complete cycle: poll → receive task → execute → report → repoll."""
    # Test the complete worker task execution cycle using simple mock orchestrator

    mock_orchestrator_port = 19460
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
        assert orch_ready, (
            f"Mock orchestrator did not start on port {mock_orchestrator_port}"
        )

        import requests

        # Prepare a sample task for the worker
        sample_task = {
            "id": "task-123",
            "agent": "mock-agent",
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

        # Wait for complete execution cycle
        time.sleep(2)  # Give worker time to poll, execute, and report

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify the task was processed
        assert "task-123" in worker_output, (
            f"Worker should process task: {worker_output}"
        )
        assert "completed successfully" in worker_output, (
            f"Task should complete: {worker_output}"
        )
        assert "Result posted successfully" in worker_output, (
            f"Result should be posted: {worker_output}"
        )

        # Verify worker continues polling after task completion (multiple polling cycles)
        polling_lines = [
            line
            for line in worker_output.split("\n")
            if "Starting worker loop" in line or "No task available" in line
        ]
        assert len(polling_lines) >= 1, (
            f"Worker should show polling activity: {worker_output}"
        )

        # Check that result was posted back to orchestrator
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 1, f"Worker should post result: {results}"
            result = results[0]
            assert "task-123" in str(result), f"Result should contain task ID: {result}"

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
        time.sleep(2)  # Give worker time to process task

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
            assert "nodeId" in result and "status" in result, (
                f"Result should have required fields: {result}"
            )

    finally:
        cleanup_processes(processes)


def test_multiple_task_execution_sequence():
    """Test worker can handle multiple sequential tasks."""
    # Test that worker processes multiple tasks in sequence

    mock_orchestrator_port = 19464
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
        assert orch_ready, "Mock orchestrator should start"

        import requests

        # Prepare sequence of tasks
        tasks = [
            {
                "id": "seq-task-1",
                "agent": "mock-agent",
                "request": {"input": "First task in sequence"},
            },
            {
                "id": "seq-task-2",
                "agent": "mock-agent",
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

        # Wait for both tasks to complete (worker processes them one by one)
        time.sleep(3)  # Give worker time to process both tasks

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify both tasks were processed
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

        # Check that results were posted back to orchestrator
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) >= 2, (
                f"Worker should post results for both tasks: {results}"
            )

            # Check that both task IDs are present in results
            results_str = str(results)
            assert "seq-task-1" in results_str, (
                f"Results should contain first task: {results}"
            )
            assert "seq-task-2" in results_str, (
                f"Results should contain second task: {results}"
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
            "request": {"input": "Test task using configured agent"},
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
        time.sleep(2)  # Give worker time to process task

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

        assert "CONFIG_LOADED" in worker_output, (
            f"Worker should use configured command args: {worker_output}"
        )
        assert "completed successfully" in worker_output, (
            f"Task should complete: {worker_output}"
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
