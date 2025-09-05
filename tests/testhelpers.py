"""Common utilities for test process and file cleanup.

These helpers centralize robust termination of subprocesses and cleanup of
temporary files to reduce duplication and flakiness across tests.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Iterable

import psutil
import requests


def _terminate_single_process(proc: subprocess.Popen, term_timeout: float = 5.0, kill_timeout: float = 2.0) -> None:
    """Terminate a single subprocess.Popen instance gracefully, then forcefully.

    Best-effort: swallows errors if the process already exited or disappears.
    """
    try:
        if proc.poll() is None:  # still running
            try:
                proc.terminate()
                proc.wait(timeout=term_timeout)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=kill_timeout)
                except Exception:
                    pass
    except Exception:
        # If proc is in a weird state, ignore; teardown should be best-effort
        pass


def cleanup_processes(processes: Iterable[subprocess.Popen]) -> None:
    """Terminate a collection of subprocesses and any lingering child processes.

    1) Attempts graceful termination for each provided process, then kill.
    2) Performs a secondary sweep over current process children recursively to
       catch any leftover orphans and ensure clean test isolation.
    """
    # Terminate tracked processes first
    for proc in list(processes):
        _terminate_single_process(proc)

    # Additional cleanup using psutil to catch any orphaned children
    try:
        current = psutil.Process()
        for child in current.children(recursive=True):
            try:
                if child.is_running():
                    try:
                        child.terminate()
                        child.wait(timeout=3)
                    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
            except psutil.NoSuchProcess:
                # Child may have exited between listing and action
                continue
    except psutil.NoSuchProcess:
        # Current process vanished? Highly unlikely in tests; ignore.
        pass


def cleanup_files(paths: Iterable[str]) -> None:
    """Best-effort unlink for temporary files created during tests."""
    for p in list(paths):
        try:
            os.unlink(p)
        except OSError:
            pass


def start_mock_orchestrator(port: int, base_dir: Path = None) -> subprocess.Popen:
    """Start the simple mock orchestrator on the specified port.

    Args:
        port: Port number for the orchestrator to listen on
        base_dir: Base directory for the project (defaults to current working directory)

    Returns:
        subprocess.Popen: The orchestrator process
    """
    if base_dir is None:
        base_dir = Path.cwd()

    orchestrator_proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "tests.integration.simple_mock_orchestrator:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir
    )
    return orchestrator_proc


def wait_for_port(port: int, timeout: float = 10) -> bool:
    """Wait for a port to become available for HTTP requests.

    Args:
        port: Port number to check
        timeout: Maximum time to wait in seconds

    Returns:
        bool: True if port is ready, False if timeout exceeded
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"http://localhost:{port}/api/health", timeout=1)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.2)
    return False
