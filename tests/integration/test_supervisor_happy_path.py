"""
Integration tests for agentbeacon process supervision.

Verifies:
- Standalone scheduler mode (--workers 0)
- Worker supervision (--workers N)
- Graceful shutdown (SIGTERM kills workers)
- Worker crash triggers restart
- Readiness message
- Log prefix format
"""

import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psutil
import pytest
import requests
from tests.testhelpers import (
    PortManager,
    orchestrator_context,
    cleanup_files,
)


def _read_lines_background(stream, line_queue):
    """Read lines from a stream into a queue. Runs on a daemon thread."""
    try:
        for line in stream:
            line_queue.put(line)
    except ValueError:
        pass  # stream closed


def test_standalone_scheduler_no_workers():
    """agentbeacon --port P --workers 0 runs scheduler only, no worker children."""
    with orchestrator_context(workers=0) as orch:
        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        # Verify process is still running
        assert orch["orchestrator"].poll() is None, (
            "agentbeacon process exited unexpectedly"
        )

        # No worker children
        try:
            parent = psutil.Process(orch["orchestrator_pid"])
            children = parent.children(recursive=False)
            worker_children = [
                c
                for c in children
                if "agentbeacon-worker" in " ".join(c.cmdline() or [])
            ]
            assert len(worker_children) == 0, (
                f"Expected 0 worker children, found {len(worker_children)}"
            )
        except psutil.NoSuchProcess:
            pass


def test_default_workers_zero():
    """agentbeacon --port P (no --workers flag) runs standalone scheduler."""
    with orchestrator_context(workers=None) as orch:
        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200

        assert orch["orchestrator"].poll() is None, (
            "agentbeacon process exited unexpectedly"
        )

        try:
            parent = psutil.Process(orch["orchestrator_pid"])
            children = parent.children(recursive=False)
            worker_children = [
                c
                for c in children
                if "agentbeacon-worker" in " ".join(c.cmdline() or [])
            ]
            assert len(worker_children) == 0
        except psutil.NoSuchProcess:
            pass


def test_merged_binary_starts_workers():
    """agentbeacon --port P --workers 2 starts scheduler + 2 workers."""
    with orchestrator_context(workers=2) as orch:
        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        orch["tracker"].assert_exact_count("worker", 2)

        # Verify worker logs have prefixed lines in output
        line_queue = queue.Queue()
        reader = threading.Thread(
            target=_read_lines_background,
            args=(orch["orchestrator"].stdout, line_queue),
            daemon=True,
        )
        reader.start()

        output_lines = []
        worker_log_found = False
        deadline = time.time() + 10

        while time.time() < deadline:
            try:
                line = line_queue.get(timeout=0.5)
                output_lines.append(line.strip())
                if any(prefix in line for prefix in ["worker-1 |", "worker-2 |"]):
                    worker_log_found = True
                    break
            except queue.Empty:
                continue

        assert worker_log_found, (
            f"Expected worker log prefixes in output: {output_lines[:20]}"
        )


def test_merged_binary_graceful_shutdown():
    """SIGTERM to merged binary shuts down all workers."""
    with orchestrator_context(workers=2) as orch:
        tracker = orch["tracker"]
        tracker.assert_exact_count("worker", 2)

        orch["orchestrator"].send_signal(signal.SIGTERM)

        try:
            orch["orchestrator"].wait(timeout=15)
        except subprocess.TimeoutExpired:
            pytest.fail("agentbeacon did not exit within 15 seconds after SIGTERM")

        time.sleep(3)

        final_worker_count = tracker.count_alive("worker")
        assert final_worker_count == 0, (
            f"Found {final_worker_count} tracked worker PIDs still alive after shutdown"
        )


def test_worker_crash_triggers_restart():
    """Killing a worker PID triggers supervisor to restart it."""
    with orchestrator_context(workers=2) as orch:
        tracker = orch["tracker"]
        initial_worker_pids = tracker.get_pids_by_type("worker")
        assert len(initial_worker_pids) == 2, "Should have 2 initial workers"

        worker_pid_to_kill = initial_worker_pids[0]
        os.kill(worker_pid_to_kill, signal.SIGKILL)

        # Wait for supervisor to detect crash and restart (1s backoff + spawn time)
        time.sleep(5)

        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200

        # Verify a new worker appeared as a child of agentbeacon
        try:
            parent = psutil.Process(orch["orchestrator_pid"])
            children = parent.children(recursive=False)
            worker_children = [
                c
                for c in children
                if "agentbeacon-worker" in " ".join(c.cmdline() or [])
            ]
            assert len(worker_children) == 2, (
                f"Expected 2 workers after restart, found {len(worker_children)}"
            )
        except psutil.NoSuchProcess:
            pytest.fail("agentbeacon parent process disappeared")


def test_partial_worker_startup_failure_cleans_up():
    """If worker binary is missing, startup aborts with no orphaned processes."""
    base_dir = Path(__file__).parent.parent.parent
    agentbeacon_bin = base_dir / "bin" / "agentbeacon"

    port_manager = PortManager()
    port = port_manager.allocate_port()

    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db.close()
    db_url = f"sqlite:{temp_db.name}?mode=rwc"

    try:
        # Copy agentbeacon to a temp dir WITHOUT the worker binary.
        # The supervisor resolves the worker path as a sibling of current_exe(),
        # so placing agentbeacon alone ensures spawn fails without mutating
        # the shared bin/ directory (safe under pytest -n4).
        with tempfile.TemporaryDirectory() as temp_bin_dir:
            temp_agentbeacon = Path(temp_bin_dir) / "agentbeacon"
            shutil.copy2(agentbeacon_bin, temp_agentbeacon)

            env = os.environ.copy()
            env["DATABASE_URL"] = db_url

            proc = subprocess.Popen(
                [
                    str(temp_agentbeacon),
                    "--port",
                    str(port),
                    "--workers",
                    "2",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                cwd=temp_bin_dir,
            )

            # Should exit with non-zero because worker spawn fails
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                pytest.fail("agentbeacon should have exited after worker spawn failure")

            assert proc.returncode != 0, (
                f"Expected non-zero exit code, got {proc.returncode}"
            )

            # Verify no orphaned worker children remain
            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = " ".join(p.info["cmdline"] or [])
                    if "agentbeacon-worker" in cmdline and str(port) in cmdline:
                        pytest.fail(
                            f"Orphaned worker found: PID {p.info['pid']}, cmdline: {cmdline}"
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

    finally:
        cleanup_files([temp_db.name])
        port_manager.release_port(port)


def test_readiness_message():
    """Merged binary emits readiness message after workers start."""
    with orchestrator_context(workers=2) as orch:
        line_queue = queue.Queue()
        reader = threading.Thread(
            target=_read_lines_background,
            args=(orch["orchestrator"].stdout, line_queue),
            daemon=True,
        )
        reader.start()

        output_lines = []
        ready_found = False
        deadline = time.time() + 15

        while time.time() < deadline:
            try:
                line = line_queue.get(timeout=0.5)
                output_lines.append(line.strip())
                if "agentbeacon ready" in line.lower():
                    ready_found = True
                    break
            except queue.Empty:
                continue

        assert ready_found, (
            f"Expected 'AgentBeacon ready' in output: {output_lines[:20]}"
        )

        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200
