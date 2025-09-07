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
from typing import Iterable, Set, List, Dict, Any

import psutil
import requests
import re
import uuid
import pytest


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
            raise RuntimeError(
                f"No available ports in range {self.MIN_PORT}-{self.MAX_PORT}"
            )

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
                sock.bind(("127.0.0.1", port))
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
        return f"sqlite:///{self._db_path}"

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


def _terminate_single_process(
    proc: subprocess.Popen, term_timeout: float = 5.0, kill_timeout: float = 2.0
) -> None:
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


def start_mock_scheduler(port: int, base_dir: Path = None) -> subprocess.Popen:
    """Start the simple mock scheduler on the specified port.

    Args:
        port: Port number for the scheduler to listen on
        base_dir: Base directory for the project (defaults to current working directory)

    Returns:
        subprocess.Popen: The scheduler process
    """
    if base_dir is None:
        base_dir = Path.cwd()

    orchestrator_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "tests.integration.simple_mock_scheduler:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
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
        time.sleep(0.1)
    return False


def start_scheduler(
    port: int, base_dir: Path = None, db_path: str = None
) -> tuple[subprocess.Popen, str]:
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

    scheduler_process = subprocess.Popen(
        [
            "./bin/agentmaestro-scheduler",
            "-port",
            str(port),
            "-driver",
            "sqlite3",
            "-db",
            temp_db_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=base_dir,
    )

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
            "process": scheduler_process,
            "url": f"http://localhost:{allocated_port}",
            "port": allocated_port,
            "db_path": temp_db_path,
        }
    finally:
        # Cleanup
        if scheduler_process:
            cleanup_processes([scheduler_process])
        if temp_db_path:
            cleanup_files([temp_db_path])
        if port_manager:
            port_manager.release_port(allocated_port)


def start_worker(
    orchestrator_url: str, interval: str = "1s", base_dir: Path = None
) -> subprocess.Popen:
    """Start the worker binary with specified configuration.

    Args:
        orchestrator_url: URL of the orchestrator to connect to
        interval: Polling interval (default: "1s")
        base_dir: Base directory for the project (defaults to test file parent directory)

    Returns:
        subprocess.Popen: The worker process

    Note:
        Worker inherits current environment variables including PYTEST_CURRENT_TEST
        for mock agent logging support.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Copy current environment so worker subprocess inherits pytest context
    worker_env = os.environ.copy()

    worker_process = subprocess.Popen(
        [
            "./bin/agentmaestro-worker",
            "-orchestrator-url",
            orchestrator_url,
            "-interval",
            interval,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
        env=worker_env,
    )

    return worker_process


def get_current_test_name(fallback: str = "unknown_test") -> str:
    """Extract and sanitize current test name from pytest environment.

    Args:
        fallback: Default name if PYTEST_CURRENT_TEST is not available

    Returns:
        Sanitized test name safe for filesystem use

    Example:
        "tests/integration::test_worker_happy_path (call)" -> "tests_integration__test_worker_happy_path"
    """

    raw_test_name = os.environ.get("PYTEST_CURRENT_TEST", "")
    if not raw_test_name:
        return fallback

    # Remove pytest phase info like "(call)" or "(setup)" from test name
    test_name_base = (
        raw_test_name.split(" (")[0] if " (" in raw_test_name else raw_test_name
    )

    # Sanitize test name for filesystem safety
    test_name = test_name_base.replace("::", "__").replace("/", "_").replace("\\", "_")
    # Remove other potentially problematic characters
    test_name = re.sub(r'[<>:"|?*]', "_", test_name)

    return test_name if test_name else fallback


@contextmanager
def worker_context(orchestrator_url: str, interval: str = "1s"):
    """Context manager for worker startup and cleanup.

    Args:
        orchestrator_url: URL of the orchestrator to connect to
        interval: Polling interval (default: "1s")

    Yields:
        subprocess.Popen: The worker process
    """
    worker_process = None

    try:
        worker_process = start_worker(orchestrator_url, interval)
        yield worker_process
    finally:
        # Cleanup
        if worker_process:
            cleanup_processes([worker_process])


def parse_agent_log(test_name: str) -> List[Dict]:
    """Parse log file for test assertions.

    Args:
        test_name: Test name (used for log file naming)

    Returns:
        List of parsed log entries, each a dict with keys:
        execution_id, node_id, timestamp, task_text
    """
    from agentmaestro.mock_agent.file_logger import parse_agent_entry

    log_file = Path(f"logs/{test_name}.log")

    # Handle missing files gracefully
    if not log_file.exists():
        return []

    try:
        content = log_file.read_text()
        if not content.strip():
            return []

        # Parse each line using unified parse_agent_entry() function
        entries = []
        for line in content.strip().split("\n"):
            # Parse all lines, including empty ones (they get default values)
            parsed = parse_agent_entry(line.strip())
            entries.append(parsed)

        return entries

    except Exception:
        # Handle any file read errors gracefully
        return []


def register_workflow(scheduler_url: str, workflow_yaml: str) -> str:
    """Register a workflow and return its reference.

    Args:
        scheduler_url: Base URL of the scheduler (e.g., "http://localhost:19456")
        workflow_yaml: YAML content of the workflow to register

    Returns:
        str: Workflow reference (e.g., "namespace/name:latest")

    Raises:
        AssertionError: If registration fails or response is invalid
    """

    response = requests.post(
        f"{scheduler_url}/api/workflows/register",
        json={"workflow_yaml": workflow_yaml},
        timeout=10,
    )
    assert response.status_code == 201, f"Workflow registration failed: {response.text}"

    data = response.json()
    assert "ref" in data, f"Registration should return ref: {data}"
    return data["ref"]


def get_a2a_endpoint(scheduler_url: str) -> str:
    """Discover A2A endpoint from agent card with proper port handling.

    Args:
        scheduler_url: Base URL of the scheduler (e.g., "http://localhost:19456")

    Returns:
        str: A2A endpoint URL with correct port for tests

    Raises:
        AssertionError: If agent card is not available or invalid
    """

    agent_card_response = requests.get(
        f"{scheduler_url}/.well-known/agent-card.json", timeout=5
    )
    assert agent_card_response.status_code == 200, (
        f"Failed to get agent card: {agent_card_response.text}"
    )

    agent_card = agent_card_response.json()
    assert "url" in agent_card, f"Agent card should have url field: {agent_card}"

    a2a_url = agent_card["url"]

    # Handle port replacement for tests
    if "localhost:9456" in a2a_url:
        # Agent card has hardcoded port 9456, replace with actual test port
        test_port = scheduler_url.split(":")[-1]
        a2a_endpoint = a2a_url.replace("localhost:9456", f"localhost:{test_port}")
    elif a2a_url.startswith("/"):
        # Relative URL - prepend base scheduler URL
        a2a_endpoint = scheduler_url + a2a_url
    else:
        # Absolute URL - use as-is
        a2a_endpoint = a2a_url

    return a2a_endpoint


def submit_workflow_via_a2a(
    scheduler_url: str, workflow_ref: str, context_id: str = None
) -> Dict[str, Any]:
    """Submit workflow execution via proper A2A JSON-RPC protocol.

    Args:
        scheduler_url: Base URL of the scheduler
        workflow_ref: Workflow reference from registration
        context_id: Optional context ID (generates one if None)

    Returns:
        dict: Task result from A2A submission

    Raises:
        AssertionError: If A2A submission fails or response is invalid
    """

    if context_id is None:
        context_id = str(uuid.uuid4())

    # A2A message with workflowRef
    message = {
        "role": "user",
        "parts": [{"kind": "data", "data": {"data": {"workflowRef": workflow_ref}}}],
        "messageId": str(uuid.uuid4()),
        "kind": "message",
    }

    # JSON-RPC request
    jsonrpc_request = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {"contextId": context_id, "messages": [message]},
        "id": str(uuid.uuid4()),
    }

    a2a_endpoint = get_a2a_endpoint(scheduler_url)
    response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=10)
    assert response.status_code == 200, (
        f"A2A workflow submission failed: {response.text}"
    )

    data = response.json()
    assert "result" in data, f"A2A response should have result: {data}"
    assert data.get("error") is None, f"A2A response should not have error: {data}"

    return data["result"]


def poll_a2a_task_status(
    scheduler_url: str, task_id: str, timeout: int = 60
) -> Dict[str, Any]:
    """Poll task status via A2A tasks/get method until completion.

    Args:
        scheduler_url: Base URL of the scheduler
        task_id: Task ID to poll
        timeout: Maximum time to wait in seconds

    Returns:
        dict: Final task status when completed/failed/cancelled

    Raises:
        AssertionError: If polling fails or task doesn't complete within timeout
    """

    a2a_endpoint = get_a2a_endpoint(scheduler_url)
    start_time = time.time()
    last_state = None

    while time.time() - start_time < timeout:
        jsonrpc_request = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "params": {"taskId": task_id},
            "id": str(uuid.uuid4()),
        }

        response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=5)
        assert response.status_code == 200, f"Task status poll failed: {response.text}"

        data = response.json()
        assert "result" in data, f"Task status response should have result: {data}"

        task = data["result"]
        task_state = task.get("status", {}).get("state")

        # Debug: Print state changes
        if task_state != last_state:
            print(f"DEBUG: Task {task_id} state changed: {last_state} -> {task_state}")
            last_state = task_state

        if task_state in ["completed", "failed", "cancelled"]:
            return task

        time.sleep(1)  # Poll every second

    pytest.fail(f"Task {task_id} did not complete within {timeout} seconds")


def start_orchestrator(
    port: int, workers: int = 2, db_url: str = None, base_dir: Path = None
) -> subprocess.Popen:
    """Start the orchestrator binary with specified configuration.

    Args:
        port: Port number for scheduler to listen on
        workers: Number of worker processes to spawn
        db_url: Complete database URL (creates temp SQLite URL if None)
        base_dir: Base directory for the project (defaults to test file parent directory)

    Returns:
        subprocess.Popen: The orchestrator process
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Create temp database URL if not provided
    if db_url is None:
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db_url = f"sqlite:///{temp_db.name}"

    env = os.environ.copy()
    env["DATABASE_URL"] = db_url

    orchestrator_proc = subprocess.Popen(
        ["./bin/agentmaestro", "-workers", str(workers), "-scheduler-port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=base_dir,
    )
    return orchestrator_proc


def count_processes_by_name(name_pattern: str) -> int:
    """Count running processes matching a name pattern.

    Args:
        name_pattern: String pattern to match in process command line

    Returns:
        int: Number of matching processes
    """
    count = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
            if name_pattern in cmdline:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return count
