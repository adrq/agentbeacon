"""Worker polling behavior tests.

Verifies workers respect polling intervals, handle scheduler unavailability,
and shut down cleanly on OS signals.

Run with: uv run pytest tests/integration/test_worker_polling.py -v
"""

import signal
import subprocess
import time
from pathlib import Path

import requests

from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_mock_scheduler,
    start_worker_with_retry_config,
    wait_for_port,
)

BASE_DIR = Path(__file__).parent.parent.parent


def test_worker_respects_polling_interval():
    """Worker respects configured sync interval when idle (no sessions available)."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"

        worker_proc = subprocess.Popen(
            [
                "./bin/agentbeacon-worker",
                "--scheduler-url",
                f"http://localhost:{port}",
                "--interval",
                "2s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BASE_DIR,
        )
        processes.append(worker_proc)

        # Allow at least 3 sync cycles (2s interval)
        time.sleep(6)

        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        # Verify startup message shows correct interval
        assert "Starting worker loop" in worker_output, (
            f"Worker should log startup: {worker_output}"
        )
        assert "every 2s" in worker_output, (
            f"Worker should report 2s interval: {worker_output}"
        )

        # Verify mock scheduler received multiple sync calls
        resp = requests.get(f"http://localhost:{port}/test/sync_log", timeout=5)
        sync_log = resp.json()
        assert len(sync_log) >= 2, (
            f"Expected at least 2 idle syncs in 6s with 2s interval, got {len(sync_log)}"
        )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_orchestrator_unavailable():
    """Worker exits gracefully with error when scheduler never becomes available."""
    processes = []

    try:
        # Point at a port nothing is listening on
        worker_proc = start_worker_with_retry_config(
            "http://localhost:19999",
            startup_attempts=3,
            reconnect_attempts=5,
            retry_delay_ms=100,
        )
        processes.append(worker_proc)

        # Should exit quickly after exhausting startup retries
        exit_code = worker_proc.wait(timeout=5)
        assert exit_code == 1, (
            f"Worker should exit with code 1 when scheduler unreachable, got {exit_code}"
        )

        worker_output = worker_proc.stdout.read() if worker_proc.stdout else ""

        # Verify error messages
        assert "scheduler unreachable" in worker_output.lower(), (
            f"Worker should mention scheduler unreachable: {worker_output}"
        )
        assert "during startup" in worker_output, (
            f"Worker should indicate startup context: {worker_output}"
        )

        # Verify retry attempts were logged
        sync_failure_count = worker_output.lower().count("sync failed")
        assert sync_failure_count >= 2, (
            f"Expected at least 2 retry logs, found {sync_failure_count}"
        )
    finally:
        cleanup_processes(processes)


def test_worker_shutdown_on_signal():
    """Worker shuts down cleanly on SIGTERM with exit code 0."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"

        worker_proc = subprocess.Popen(
            [
                "./bin/agentbeacon-worker",
                "--scheduler-url",
                f"http://localhost:{port}",
                "--interval",
                "5s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BASE_DIR,
        )
        processes.append(worker_proc)

        time.sleep(2)
        assert worker_proc.poll() is None, "Worker should be running"

        worker_proc.send_signal(signal.SIGTERM)

        try:
            exit_code = worker_proc.wait(timeout=10)
            assert exit_code == 0, (
                f"Worker should exit cleanly on SIGTERM, got exit code {exit_code}"
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                "Worker did not shutdown within 10 seconds after SIGTERM"
            )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)
