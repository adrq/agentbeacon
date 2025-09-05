"""Common utilities for test process and file cleanup.

These helpers centralize robust termination of subprocesses and cleanup of
temporary files to reduce duplication and flakiness across tests.
"""

from __future__ import annotations

import os
import subprocess
from typing import Iterable

import psutil


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
