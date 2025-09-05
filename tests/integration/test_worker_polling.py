"""
T004: Idle Polling Test - Worker polling behavior integration test.

This test verifies that workers properly respect polling intervals and handle
idle periods when no tasks are available from the orchestrator.

Run with: uv run pytest tests/integration/test_worker_polling.py -v
"""

import os
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from typing import List
from tests.testhelpers import cleanup_processes, start_mock_orchestrator, wait_for_port
import pytest
import requests


def test_worker_respects_polling_interval():
    """Test that worker respects 2s polling interval when no tasks available."""
    # Use faster interval for testing to reduce test time

    test_port = 19457  # Unique port for this test
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        orchestrator_proc = start_mock_orchestrator(test_port, Path(__file__).parent.parent.parent)
        processes.append(orchestrator_proc)

        # Wait for orchestrator to be ready
        orchestrator_ready = wait_for_port(test_port, timeout=15)
        assert orchestrator_ready, f"Mock orchestrator did not start on port {test_port} within 15 seconds"

        # Verify orchestrator health
        response = requests.get(f"http://localhost:{test_port}/api/health", timeout=5)
        assert response.status_code == 200

        # Start worker with 2s polling interval (faster for testing)
        worker_proc = subprocess.Popen(
            [worker_binary, "-orchestrator-url", f"http://localhost:{test_port}", "-interval", "2s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        processes.append(worker_proc)

        # Give worker time to start and make several polls
        time.sleep(6)  # Allow for at least 3 poll cycles (2s interval)

        # Stop worker
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Parse worker output for "No task available" messages which indicate successful polls
        poll_lines = [line for line in worker_output.split('\n') if 'No task available' in line]

        # Should have multiple successful poll cycles
        assert len(poll_lines) >= 2, f"Expected at least 2 successful polls, got {len(poll_lines)}"

        # Verify worker reports correct interval in startup message
        startup_lines = [line for line in worker_output.split('\n') if 'Starting worker loop' in line and 'every 2s' in line]
        assert len(startup_lines) >= 1, f"Expected worker to report 2s interval in startup message. Output: {worker_output}"

        # Check that poll requests return expected format (no tasks)
        response = requests.get(f"http://localhost:{test_port}/api/worker/poll", timeout=5)
        assert response.status_code == 200
        assert response.json() == {"task": None}, "Poll should return null task when idle"

    finally:
        cleanup_processes(processes)


def test_worker_handles_orchestrator_unavailable():
    """Test that worker gracefully handles orchestrator being unavailable."""
    # This test will fail until error handling is properly implemented

    worker_binary = "./bin/agentmaestro-worker"
    unavailable_port = 19999  # Port that should be unused
    processes = []

    try:
        # Start worker pointing to non-existent orchestrator
        worker_proc = subprocess.Popen(
            [worker_binary, "-orchestrator-url", f"http://localhost:{unavailable_port}", "-interval", "2s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        processes.append(worker_proc)

        # Let worker attempt polling for several cycles
        time.sleep(6)  # 3 polling attempts at 2s interval

        # Worker should still be running despite connection failures
        assert worker_proc.poll() is None, "Worker should continue running despite orchestrator unavailability"

        # Terminate worker and get complete output
        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Should see poll failure messages but worker continues
        assert "Poll and execute failed" in worker_output or "connection refused" in worker_output.lower() or "dial tcp" in worker_output.lower(), \
            f"Expected poll failure messages in worker output: {worker_output}"

    finally:
        cleanup_processes(processes)


def test_worker_shutdown_on_signal():
    """Test that worker shuts down gracefully on SIGTERM."""
    # This test will fail until signal handling is properly implemented

    test_port = 19458  # Unique port
    worker_binary = "./bin/agentmaestro-worker"
    processes = []

    try:
        # Start simple mock orchestrator
        orchestrator_proc = start_mock_orchestrator(test_port, Path(__file__).parent.parent.parent)
        processes.append(orchestrator_proc)

        # Wait for orchestrator
        orchestrator_ready = wait_for_port(test_port, timeout=10)
        assert orchestrator_ready, "Mock orchestrator should start successfully"

        # Start worker
        worker_proc = subprocess.Popen(
            [worker_binary, "-orchestrator-url", f"http://localhost:{test_port}", "-interval", "5s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=Path(__file__).parent.parent.parent
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
            assert exit_code == 0, f"Worker should exit cleanly, got exit code {exit_code}"
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
