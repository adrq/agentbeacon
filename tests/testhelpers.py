"""Common utilities for test process and file cleanup.

These helpers centralize robust termination of subprocesses and cleanup of
temporary files to reduce duplication and flakiness across tests.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Set

import psutil
import requests


class PortManager:
    """Thread-safe port allocation manager for test isolation.

    Allocates ports in range 19456-19500 with socket-based availability checking.
    """

    MIN_PORT = 19456
    MAX_PORT = 19500

    def __init__(self):
        self._lock = threading.Lock()
        self._allocated: Set[int] = set()

    def allocate_port(self) -> int:
        """Allocate an unused port from the range.

        Returns:
            int: Allocated port number

        Raises:
            RuntimeError: If no ports are available
        """
        with self._lock:
            for port in range(self.MIN_PORT, self.MAX_PORT + 1):
                if port not in self._allocated and self._is_port_available(port):
                    self._allocated.add(port)
                    return port
            raise RuntimeError(f"No available ports in range {self.MIN_PORT}-{self.MAX_PORT}")

    def release_port(self, port: int) -> None:
        """Release a previously allocated port.

        Args:
            port: Port number to release
        """
        with self._lock:
            self._allocated.discard(port)

    @contextmanager
    def port_context(self):
        """Context manager for automatic port cleanup.

        Yields:
            int: Allocated port number
        """
        port = self.allocate_port()
        try:
            yield port
        finally:
            self.release_port(port)

    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available by attempting to bind to it.

        Args:
            port: Port number to check

        Returns:
            bool: True if port is available
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(('127.0.0.1', port))
                return True
        except OSError:
            return False


class TempDatabase:
    """Temporary SQLite database management for testing.

    Creates isolated SQLite databases with automatic cleanup.
    """

    def __init__(self):
        self._temp_dir = None
        self._db_path = None

    def create(self) -> str:
        """Create a temporary database and return its URL.

        Returns:
            str: SQLite database URL
        """
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "test.db"
        return f"sqlite://{self._db_path}"

    def cleanup(self) -> None:
        """Remove the database and temporary directory."""
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                # Best-effort cleanup
                pass
            finally:
                self._temp_dir = None
                self._db_path = None

    def __enter__(self):
        """Context manager entry."""
        return self.create()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.cleanup()


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


def start_scheduler(port: int, base_dir: Path = None, db_path: str = None) -> tuple[subprocess.Popen, str]:
    """Start the scheduler binary with specified configuration.

    Args:
        port: Port number for scheduler to listen on
        base_dir: Base directory for the project (defaults to test file parent directory)
        db_path: Path to existing database file (creates temp file if None)

    Returns:
        tuple: (scheduler_process, temp_db_path)

    Raises:
        RuntimeError: If scheduler fails to start within timeout
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Create temp database if not provided
    temp_db_path = db_path
    if db_path is None:
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        temp_db_path = temp_db.name

    scheduler_process = subprocess.Popen([
        "./bin/agentmaestro-scheduler",
        "-port", str(port),
        "-driver", "sqlite3",
        "-db", temp_db_path
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    cwd=base_dir)

    # Wait for scheduler ready
    if not wait_for_port(port, timeout=15):
        # Cleanup failed process
        try:
            scheduler_process.terminate()
            scheduler_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            scheduler_process.kill()
        raise RuntimeError("Scheduler failed to start within timeout")

    return scheduler_process, temp_db_path


@contextmanager
def scheduler_context(port: int = None):
    """Context manager for scheduler startup and cleanup.

    Args:
        port: Port number (allocates one if None)

    Yields:
        dict: Contains 'process', 'url', 'port', 'db_path'
    """
    port_manager = PortManager() if port is None else None
    allocated_port = port_manager.allocate_port() if port_manager else port
    scheduler_process = None
    temp_db_path = None

    try:
        scheduler_process, temp_db_path = start_scheduler(allocated_port)
        yield {
            'process': scheduler_process,
            'url': f"http://localhost:{allocated_port}",
            'port': allocated_port,
            'db_path': temp_db_path
        }
    finally:
        # Cleanup
        if scheduler_process:
            cleanup_processes([scheduler_process])
        if temp_db_path:
            cleanup_files([temp_db_path])
        if port_manager:
            port_manager.release_port(allocated_port)
