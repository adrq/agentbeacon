"""
T004: Idle Polling Test - Worker polling behavior integration test.

This test verifies that workers properly respect polling intervals and handle
idle periods when no tasks are available from the orchestrator.

Run with: uv run pytest tests/integration/test_worker_polling.py -v
"""

import subprocess
import time
from pathlib import Path
from tests.testhelpers import (
    cleanup_processes,
    start_mock_scheduler,
    start_worker_with_retry_config,
    wait_for_port,
    start_and_wait_for_a2a_agent,
)
import pytest
import requests


def test_worker_respects_polling_interval():
    """Test that worker respects 2s sync interval when no tasks available."""
    # Use faster interval for testing to reduce test time

    test_port = 19457  # Unique port for this test
    worker_binary = "./bin/agentbeacon-worker"
    processes = []

    try:
        # Start A2A mock agent
        agent_proc = start_and_wait_for_a2a_agent(
            18765, Path(__file__).parent.parent.parent
        )
        processes.append(agent_proc)

        # Start simple mock orchestrator with sync endpoint
        scheduler_proc = start_mock_scheduler(
            test_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for scheduler to be ready
        scheduler_ready = wait_for_port(test_port, timeout=15)
        assert scheduler_ready, (
            f"Mock scheduler did not start on port {test_port} within 15 seconds"
        )

        # Verify scheduler health
        response = requests.get(f"http://localhost:{test_port}/api/health", timeout=5)
        assert response.status_code == 200

        # Start worker with 2s sync interval (faster for testing)
        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "--scheduler-url",
                f"http://localhost:{test_port}",
                "--interval",
                "2s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        # Give worker time to start and make several sync calls
        time.sleep(6)  # Allow for at least 3 sync cycles (2s interval)

        # Stop worker
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify sync endpoint usage instead of poll
        assert (
            "syncing with" in worker_output and "/api/worker/sync" in worker_output
        ), f"Worker should use sync endpoint instead of poll: {worker_output}"

        # Verify worker receives no_action responses when idle (no tasks available)
        assert "No action from sync response" in worker_output, (
            f"Worker should receive no_action when idle: {worker_output}"
        )

        # Parse worker output for sync activity - look for sync calls or "no_action" responses
        sync_activity = [
            line
            for line in worker_output.split("\n")
            if "sync" in line.lower() or "no_action" in line or "idle" in line
        ]

        # Should have multiple sync cycles
        assert len(sync_activity) >= 2, (
            f"Expected at least 2 sync activities, got {len(sync_activity)}: {worker_output}"
        )

        # Verify worker reports correct interval in startup message
        startup_lines = [
            line
            for line in worker_output.split("\n")
            if "Starting worker loop" in line and "every 2s" in line
        ]
        assert len(startup_lines) >= 1, (
            f"Expected worker to report 2s interval in startup message. Output: {worker_output}"
        )

        # Verify sync calls were made by checking orchestrator received them
        response = requests.get(f"http://localhost:{test_port}/sync_count", timeout=5)
        if response.status_code == 200:
            sync_data = response.json()
            assert sync_data["count"] > 0, (
                f"Orchestrator should receive sync calls from worker: {sync_data}"
            )

    finally:
        cleanup_processes(processes)


def test_worker_handles_orchestrator_unavailable():
    """Test that worker exits gracefully when scheduler never available."""
    unavailable_port = 19999  # Port that should be unused
    processes = []

    try:
        # Start worker pointing to non-existent orchestrator
        # Use fast startup failure: 3 attempts × 100ms = 300ms
        worker_proc = start_worker_with_retry_config(
            f"http://localhost:{unavailable_port}",
            startup_attempts=3,
            reconnect_attempts=5,  # Unused since never connects
            retry_delay_ms=100,
        )
        processes.append(worker_proc)

        # Wait for worker to exhaust startup retries and exit
        # Should exit in ~300ms, wait up to 2s with buffer
        exit_code = worker_proc.wait(timeout=2)

        # Verify proper exit behavior
        assert exit_code == 1, (
            "Worker should exit with error code when scheduler unavailable"
        )

        # Get output and verify error logging
        worker_output = worker_proc.stdout.read() if worker_proc.stdout else ""

        assert "scheduler unreachable - exiting worker" in worker_output, (
            "Worker should log exit reason"
        )
        assert "during startup" in worker_output, (
            "Worker should indicate startup failure context"
        )

        # Verify retry attempts logged
        sync_failure_count = worker_output.lower().count("sync failed")
        assert sync_failure_count >= 2, (
            f"Worker should log multiple retry attempts, found {sync_failure_count}"
        )

        # Should show worker attempting sync
        assert "/api/worker/sync" in worker_output, (
            f"Worker should attempt sync calls: {worker_output}"
        )

        # Verify sync failures were logged (already validated by sync_failure_count above)
        assert "failed to send sync request" in worker_output, (
            f"Worker should report sync connection failures: {worker_output}"
        )

    finally:
        cleanup_processes(processes)


def test_worker_shutdown_on_signal():
    """Test that worker shuts down gracefully on SIGTERM."""
    # This test will fail until signal handling is properly implemented

    test_port = 19458  # Unique port
    worker_binary = "./bin/agentbeacon-worker"
    processes = []

    try:
        # Start A2A mock agent
        agent_proc = start_and_wait_for_a2a_agent(
            18765, Path(__file__).parent.parent.parent
        )
        processes.append(agent_proc)

        # Start simple mock orchestrator
        scheduler_proc = start_mock_scheduler(
            test_port, Path(__file__).parent.parent.parent
        )
        processes.append(scheduler_proc)

        # Wait for orchestrator
        orchestrator_ready = wait_for_port(test_port, timeout=10)
        assert orchestrator_ready, "Mock orchestrator should start successfully"

        # Start worker
        worker_proc = subprocess.Popen(
            [
                worker_binary,
                "--scheduler-url",
                f"http://localhost:{test_port}",
                "--interval",
                "5s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        processes.append(worker_proc)

        # Let worker start polling
        time.sleep(2)
        assert worker_proc.poll() is None, "Worker should be running"

        # Send SIGTERM to worker
        import signal

        worker_proc.send_signal(signal.SIGTERM)

        # Worker should shut down gracefully within reasonable time
        try:
            exit_code = worker_proc.wait(timeout=10)
            assert exit_code == 0, (
                f"Worker should exit cleanly, got exit code {exit_code}"
            )
        except subprocess.TimeoutExpired:
            pytest.fail("Worker did not shutdown within 10 seconds after SIGTERM")

    finally:
        cleanup_processes(processes)


# class _HTTPRequestMonitor:
#     """Helper class to monitor HTTP requests to specific endpoints."""

#     def __init__(self, port: int, endpoint: str):
#         self.port = port
#         self.endpoint = endpoint
#         self.should_stop = False

#     def monitor_requests(self, timestamps: List[float], duration: float):
#         """Monitor requests for specified duration, recording timestamps."""
#         start_time = time.time()
#         last_check = start_time

#         while time.time() - start_time < duration and not self.should_stop:
#             current_time = time.time()

#             # Check if enough time has passed to warrant another check (avoid tight loop)
#             if current_time - last_check >= 0.5:
#                 try:
#                     # Make request to detect if worker is polling
#                     response = requests.get(f"http://localhost:{self.port}{self.endpoint}", timeout=1)
#                     if response.status_code == 200:
#                         timestamps.append(current_time)
#                 except requests.RequestException:
#                     pass  # Expected when no request in progress

#                 last_check = current_time

#             time.sleep(0.1)  # Small sleep to avoid excessive CPU usage

#     def stop(self):
#         """Signal the monitor to stop."""
#         self.should_stop = True
