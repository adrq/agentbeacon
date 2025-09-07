"""
T005: Happy Path Test - Complete worker task execution cycle integration test.

This test verifies the complete worker lifecycle:
1. Worker polls and receives a task from orchestrator
2. Worker executes the task using mock-agent
3. Worker reports result back to orchestrator
4. Worker resumes polling for next task

Run with: uv run pytest tests/integration/test_worker_happy_path.py -v
"""

import time
import requests
from pathlib import Path
from tests.testhelpers import (
    cleanup_processes,
    start_mock_scheduler,
    wait_for_port,
    parse_agent_log,
    get_current_test_name,
    start_worker,
)


def test_worker_complete_task_execution_cycle():
    """Test complete cycle: sync idle → receive task → sync working → sync result → sync idle."""
    # Test the complete worker sync cycle using simple mock orchestrator

    mock_orchestrator_port = 19460
    processes = []

    try:
        # Start simple mock orchestrator with sync endpoint
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, (
            f"Mock scheduler did not start on port {mock_orchestrator_port}"
        )

        # Prepare a sample task for the worker
        sample_task = {
            "id": "task-123",
            "agent": "mock-agent",
            "executionId": "exec-123",
            "request": {"input": "Hello, please respond with a greeting"},
        }

        # Add task to scheduler
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=sample_task
        )
        assert response.status_code == 200

        # Clear any existing log entries for this test before starting worker
        test_name = get_current_test_name("test_worker_complete_task_execution_cycle")
        log_file = Path(f"logs/{test_name}.log")
        if log_file.exists():
            log_file.unlink()

        # Start worker
        worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
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

        # T017: Verify backward compatibility - plain text prompts get default values in logs
        # Parse log entries (test_name already determined earlier)
        log_entries = parse_agent_log(test_name)

        # Should have at least 1 log entry for the plain text task
        assert len(log_entries) == 1, (
            f"Expected 1 log entry, got {len(log_entries)}: {log_entries}"
        )

        # Verify plain text prompt gets default values
        first_entry = log_entries[0]
        assert first_entry.get("execution_id") == "default", (
            f"Plain text prompt should get default execution_id, got: {first_entry.get('execution_id')}"
        )
        assert first_entry.get("node_id") == "default", (
            f"Plain text prompt should get default node_id, got: {first_entry.get('node_id')}"
        )
        assert "Hello, please respond with a greeting" in first_entry.get(
            "task_text", ""
        ), f"Task text should contain original prompt: {first_entry.get('task_text')}"

        # Verify timestamp was properly set (not "NOW")
        timestamp = first_entry.get("timestamp", "")
        assert timestamp != "NOW", (
            f"Timestamp should be replaced, not 'NOW': {timestamp}"
        )
        assert len(timestamp) == 20, (
            f"Timestamp should be 20 characters ISO format: {timestamp}"
        )

    finally:
        cleanup_processes(processes)


def test_worker_handles_task_with_output():
    """Test worker properly captures and reports task output."""
    # Test that worker captures output from mock-agent and includes it in result

    mock_orchestrator_port = 19462
    processes = []

    try:
        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock orchestrator should start successfully"

        import requests

        # Task that should produce output
        output_task = {
            "id": "output-task-456",
            "agent": "mock-agent",
            "request": {"input": "Generate a test response with some output"},
        }

        # Add task to scheduler
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=output_task
        )
        assert response.status_code == 200

        # Start worker
        worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
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
    processes = []

    try:
        # Start simple mock orchestrator with sync endpoint support
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock orchestrator should start"

        import requests

        # Prepare sequence of tasks with same execution ID and bracketed format prompts
        tasks = [
            {
                "id": "seq-task-1",
                "agent": "mock-agent",
                "executionId": "workflow-exec-123",
                "request": {
                    "input": "[workflow-exec-123][node-1] NOW First task in sequence"
                },
            },
            {
                "id": "seq-task-2",
                "agent": "mock-agent",
                "executionId": "workflow-exec-123",
                "request": {
                    "input": "[workflow-exec-123][node-2] NOW Second task in sequence"
                },
            },
        ]

        # Add tasks to orchestrator
        for task in tasks:
            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=task
            )
            assert response.status_code == 200

        # Clear any existing log entries for this test before starting worker
        test_name = get_current_test_name("test_multiple_task_execution_sequence")
        log_file = Path(f"logs/{test_name}.log")
        if log_file.exists():
            log_file.unlink()

        # Start worker
        worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
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

        # Verify workflow execution logging with bracketed format
        # Parse log entries (test_name already determined earlier)
        log_entries = parse_agent_log(test_name)

        # Should have exactly 2 log entries for the 2 tasks
        assert len(log_entries) == 2, (
            f"Expected exactly 2 log entries, got {len(log_entries)}: {log_entries}"
        )

        # Verify first entry (seq-task-1): execution order and content in one go
        assert log_entries[0].get("execution_id") == "workflow-exec-123", (
            f"First log entry should have workflow-exec-123, got: {log_entries[0].get('execution_id')}"
        )
        assert log_entries[0].get("node_id") == "node-1", (
            f"First log entry should have node-1, got: {log_entries[0].get('node_id')}"
        )
        assert "First task in sequence" in log_entries[0].get("task_text", ""), (
            f"First log entry should contain 'First task in sequence': {log_entries[0].get('task_text')}"
        )

        # Verify second entry (seq-task-2): same execution ID, different node ID
        assert log_entries[1].get("execution_id") == "workflow-exec-123", (
            f"Second log entry should have same execution ID workflow-exec-123, got: {log_entries[1].get('execution_id')}"
        )
        assert log_entries[1].get("node_id") == "node-2", (
            f"Second log entry should have node-2, got: {log_entries[1].get('node_id')}"
        )
        assert "Second task in sequence" in log_entries[1].get("task_text", ""), (
            f"Second log entry should contain 'Second task in sequence': {log_entries[1].get('task_text')}"
        )

    finally:
        cleanup_processes(processes)


def test_worker_uses_agents_yaml_config():
    """Test worker correctly looks up agent configurations from agents.yaml file."""
    # Test that worker reads agent config from examples/agents.yaml and uses configured command/args

    mock_orchestrator_port = 19465
    processes = []

    try:
        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock orchestrator should start successfully"

        import requests

        # Task that specifies test-config-agent from examples/agents.yaml
        config_task = {
            "id": "config-test-task-789",
            "agent": "test-config-agent",  # This agent is in examples/agents.yaml with special args
            "request": {"prompt": "Test task using configured agent"},
        }

        # Add task to scheduler
        response = requests.post(
            f"http://localhost:{mock_orchestrator_port}/add_task", json=config_task
        )
        assert response.status_code == 200

        # Start worker (uses hardcoded examples/agents.yaml)
        worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
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


def test_mixed_bracketed_and_plain_text_compatibility():
    """T017: Test mixed bracketed and non-bracketed prompts work together."""
    # Test that workflows can contain both bracketed and plain text prompts

    mock_orchestrator_port = 19467
    processes = []

    try:
        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            mock_orchestrator_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
        assert scheduler_ready, "Mock orchestrator should start"

        import requests

        # Mix of bracketed and plain text prompts
        mixed_tasks = [
            {
                "id": "plain-task-1",
                "agent": "mock-agent",
                "request": {"input": "Plain text task without brackets"},
            },
            {
                "id": "bracketed-task-1",
                "agent": "mock-agent",
                "request": {
                    "input": "[workflow-123][step-1] NOW Bracketed task with format"
                },
            },
            {
                "id": "plain-task-2",
                "agent": "mock-agent",
                "request": {"input": "Another plain text task"},
            },
        ]

        # Add all tasks to orchestrator
        for task in mixed_tasks:
            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=task
            )
            assert response.status_code == 200

        # Clear any existing log entries for this test before starting worker
        test_name = get_current_test_name(
            "test_mixed_bracketed_and_plain_text_compatibility"
        )
        log_file = Path(f"logs/{test_name}.log")
        if log_file.exists():
            log_file.unlink()

        # Start worker
        worker_proc = start_worker(f"http://localhost:{mock_orchestrator_port}")
        processes.append(worker_proc)

        # Wait for all tasks to complete
        time.sleep(6)  # Give worker time to process all 3 tasks

        # Stop worker and get output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify all tasks were processed
        assert "plain-task-1" in worker_output
        assert "bracketed-task-1" in worker_output
        assert "plain-task-2" in worker_output

        # Count successful completions
        completed_count = worker_output.count("completed successfully")
        assert completed_count >= 3, (
            f"Expected 3 completed tasks, got {completed_count}: {worker_output}"
        )

        # Verify logging behavior for mixed formats
        # Parse log entries (test_name already determined earlier)
        log_entries = parse_agent_log(test_name)

        # Should have exactly 3 log entries for the 3 tasks
        assert len(log_entries) == 3, (
            f"Expected exactly 3 log entries, got {len(log_entries)}: {log_entries}"
        )

        # Verify first entry (plain-task-1): execution order and content in one go
        assert log_entries[0].get("execution_id") == "default", (
            f"First log entry should have default execution_id, got: {log_entries[0].get('execution_id')}"
        )
        assert log_entries[0].get("node_id") == "default", (
            f"First log entry should have default node_id, got: {log_entries[0].get('node_id')}"
        )
        assert "Plain text task without brackets" in log_entries[0].get(
            "task_text", ""
        ), (
            f"First log entry should contain 'Plain text task without brackets': {log_entries[0].get('task_text')}"
        )

        # Verify second entry (bracketed-task-1): execution order and content in one go
        assert log_entries[1].get("execution_id") == "workflow-123", (
            f"Second log entry should have workflow-123, got: {log_entries[1].get('execution_id')}"
        )
        assert log_entries[1].get("node_id") == "step-1", (
            f"Second log entry should have step-1, got: {log_entries[1].get('node_id')}"
        )
        assert "Bracketed task with format" in log_entries[1].get("task_text", ""), (
            f"Second log entry should contain 'Bracketed task with format': {log_entries[1].get('task_text')}"
        )

        # Verify third entry (plain-task-2): execution order and content in one go
        assert log_entries[2].get("execution_id") == "default", (
            f"Third log entry should have default execution_id, got: {log_entries[2].get('execution_id')}"
        )
        assert log_entries[2].get("node_id") == "default", (
            f"Third log entry should have default node_id, got: {log_entries[2].get('node_id')}"
        )
        assert "Another plain text task" in log_entries[2].get("task_text", ""), (
            f"Third log entry should contain 'Another plain text task': {log_entries[2].get('task_text')}"
        )

        # Verify no breaking changes - all tasks should complete successfully
        response = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
        if response.status_code == 200:
            results = response.json()
            assert len(results) == 3, (
                f"Should have exactly 3 results for all 3 tasks: {results}"
            )

    finally:
        cleanup_processes(processes)
