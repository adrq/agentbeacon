"""Common utilities for test process and file cleanup.

These helpers centralize robust termination of subprocesses and cleanup of
temporary files to reduce duplication and flakiness across tests.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Set, List, Dict, Any

import shutil
import sqlite3

import psycopg2

import httpx
import psutil
import requests
import re
import uuid
import pytest
import yaml


class PortManager:
    """Thread-safe port allocation manager for test isolation.

    Allocates ports from different ranges for different process types:
    - Scheduler: 19456-19700
    - Agents: 18700-18799

    Provides socket-based availability checking to avoid conflicts.
    """

    SCHEDULER_MIN_PORT = 19456
    SCHEDULER_MAX_PORT = 19700
    AGENT_MIN_PORT = 18700
    AGENT_MAX_PORT = 18799

    # Legacy aliases for backward compatibility
    MIN_PORT = SCHEDULER_MIN_PORT
    MAX_PORT = SCHEDULER_MAX_PORT

    def __init__(self):
        self._lock = threading.Lock()
        self._allocated: Set[int] = set()

    def allocate_port(self, min_port: int = None, max_port: int = None) -> int:
        """Allocate an unused port from specified or default range.

        Args:
            min_port: Minimum port in range (defaults to SCHEDULER_MIN_PORT)
            max_port: Maximum port in range (defaults to SCHEDULER_MAX_PORT)

        Returns:
            int: Allocated port number

        Raises:
            RuntimeError: If no ports are available
        """
        if min_port is None:
            min_port = self.SCHEDULER_MIN_PORT
        if max_port is None:
            max_port = self.SCHEDULER_MAX_PORT

        with self._lock:
            for port in range(min_port, max_port + 1):
                if port not in self._allocated and self._is_port_available(port):
                    self._allocated.add(port)
                    return port
            raise RuntimeError(f"No available ports in range {min_port}-{max_port}")

    def allocate_scheduler_port(self) -> int:
        """Allocate a port from the scheduler range (19456-19500).

        Returns:
            int: Allocated scheduler port
        """
        return self.allocate_port(self.SCHEDULER_MIN_PORT, self.SCHEDULER_MAX_PORT)

    def allocate_agent_port(self) -> int:
        """Allocate a port from the agent range (18700-18799).

        Returns:
            int: Allocated agent port
        """
        return self.allocate_port(self.AGENT_MIN_PORT, self.AGENT_MAX_PORT)

    def release_port(self, port: int) -> None:
        """Release a previously allocated port.

        Args:
            port: Port number to release
        """
        with self._lock:
            self._allocated.discard(port)

    @contextmanager
    def port_context(self, port_type: str = "scheduler"):
        """Context manager for automatic port cleanup.

        Args:
            port_type: Type of port to allocate ("scheduler" or "agent")

        Yields:
            int: Allocated port number
        """
        if port_type == "agent":
            port = self.allocate_agent_port()
        else:
            port = self.allocate_scheduler_port()

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
        return f"sqlite:{self._db_path}?mode=rwc"

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


class ProcessTracker:
    """Track and manage test-spawned processes for isolation from external instances.

    Provides PID-based process tracking to avoid conflicts when AgentBeacon
    is running separately outside of tests.

    Example:
        tracker = ProcessTracker()
        orchestrator_proc = start_orchestrator(port=19456)
        tracker.register_process(orchestrator_proc, "orchestrator", {"port": 19456})

        # Later: count only our processes
        count = tracker.count_alive("scheduler")

        # Cleanup only our processes
        tracker.cleanup_all()
    """

    def __init__(self, test_name: str = "unknown"):
        """Initialize process tracker.

        Args:
            test_name: Name of test for debugging purposes
        """
        self._lock = threading.Lock()
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._test_name = test_name

    def register_process(
        self,
        proc: subprocess.Popen,
        process_type: str,
        metadata: Dict[str, Any] = None,
    ) -> int:
        """Register a spawned process for tracking.

        Args:
            proc: subprocess.Popen instance
            process_type: Type identifier (e.g., "orchestrator", "scheduler", "worker", "agent")
            metadata: Optional metadata dict (port, db_path, etc.)

        Returns:
            int: Process ID of registered process
        """
        with self._lock:
            pid = proc.pid
            self._processes[pid] = {
                "proc": proc,
                "type": process_type,
                "metadata": metadata or {},
                "parent_pid": None,
            }
            return pid

    def register_child_pid(
        self, child_pid: int, process_type: str, parent_pid: int, metadata: Dict = None
    ) -> None:
        """Register a child PID (e.g., orchestrator's spawned scheduler/workers).

        Args:
            child_pid: PID of child process
            process_type: Type identifier
            parent_pid: PID of parent process
            metadata: Optional metadata dict
        """
        with self._lock:
            self._processes[child_pid] = {
                "proc": None,  # We don't have Popen object for children
                "type": process_type,
                "metadata": metadata or {},
                "parent_pid": parent_pid,
            }

    def is_alive(self, pid: int) -> bool:
        """Check if a tracked PID is still alive.

        Args:
            pid: Process ID to check

        Returns:
            bool: True if process exists and is running
        """
        try:
            proc = psutil.Process(pid)
            return proc.is_running()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def count_alive(self, process_type: str = None) -> int:
        """Count tracked processes that are still alive.

        Args:
            process_type: Optional type filter (e.g., "scheduler", "worker")
                         If None, counts all tracked processes

        Returns:
            int: Number of alive tracked processes matching criteria
        """
        with self._lock:
            count = 0
            for pid, info in self._processes.items():
                if process_type is None or info["type"] == process_type:
                    if self.is_alive(pid):
                        count += 1
            return count

    def get_pids_by_type(self, process_type: str) -> List[int]:
        """Get list of tracked PIDs by type.

        Args:
            process_type: Type identifier to filter by

        Returns:
            List of PIDs matching the type
        """
        with self._lock:
            return [
                pid
                for pid, info in self._processes.items()
                if info["type"] == process_type
            ]

    def assert_exact_count(self, process_type: str, expected: int) -> None:
        """Assert that exactly N processes of given type are alive.

        Args:
            process_type: Type to check
            expected: Expected count

        Raises:
            AssertionError: If count doesn't match expected
        """
        actual = self.count_alive(process_type)
        assert actual == expected, (
            f"Expected exactly {expected} {process_type} process(es), "
            f"found {actual} in test {self._test_name}"
        )

    def cleanup_all(self, term_timeout: float = 5.0, kill_timeout: float = 2.0) -> None:
        """Terminate all tracked processes gracefully, then forcefully.

        Args:
            term_timeout: Seconds to wait after SIGTERM
            kill_timeout: Seconds to wait after SIGKILL
        """
        with self._lock:
            processes = list(self._processes.values())

        for info in processes:
            proc = info.get("proc")
            if proc is not None:
                _terminate_single_process(proc, term_timeout, kill_timeout)

    def discover_orchestrator_children(
        self, orchestrator_pid: int, timeout: float = 5.0
    ) -> Dict[str, List[int]]:
        """Discover and register child processes spawned by orchestrator.

        Polls for scheduler and worker processes that are children of the orchestrator.

        Args:
            orchestrator_pid: PID of orchestrator parent process
            timeout: Maximum time to wait for children to appear

        Returns:
            Dict with keys "scheduler" and "workers" containing discovered PIDs
        """
        start_time = time.time()
        found_scheduler = None
        found_workers = []

        while time.time() - start_time < timeout:
            try:
                parent = psutil.Process(orchestrator_pid)
                children = parent.children(recursive=False)

                for child in children:
                    try:
                        cmdline = " ".join(child.cmdline() or [])
                        child_pid = child.pid

                        if (
                            "agentbeacon-scheduler" in cmdline
                            and found_scheduler is None
                        ):
                            self.register_child_pid(
                                child_pid,
                                "scheduler",
                                orchestrator_pid,
                                {"discovered": True},
                            )
                            found_scheduler = child_pid

                        elif (
                            "agentbeacon-worker" in cmdline
                            and child_pid not in found_workers
                        ):
                            self.register_child_pid(
                                child_pid,
                                "worker",
                                orchestrator_pid,
                                {"discovered": True},
                            )
                            found_workers.append(child_pid)

                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                # If we found at least scheduler, give a bit more time for workers
                if found_scheduler and len(found_workers) > 0:
                    time.sleep(0.2)
                    break

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            time.sleep(0.1)

        return {
            "scheduler": [found_scheduler] if found_scheduler else [],
            "workers": found_workers,
        }

    def log_external_processes(self, process_type: str = None) -> None:
        """Log any external processes matching our patterns (for debugging).

        Args:
            process_type: Optional type to check ("scheduler", "worker", etc.)
        """
        patterns = {
            "scheduler": "agentbeacon-scheduler",
            "worker": "agentbeacon-worker",
            "orchestrator": "agentbeacon",
        }

        search_patterns = (
            [patterns[process_type]] if process_type else patterns.values()
        )

        external_found = []
        for pattern in search_patterns:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    if pattern in cmdline and proc.info["pid"] not in self._processes:
                        external_found.append((proc.info["pid"], cmdline))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        if external_found:
            print(
                f"\n⚠️  WARNING [{self._test_name}]: Found external agentbeacon processes "
                f"(not spawned by this test):"
            )
            for pid, cmdline in external_found:
                print(f"  - PID {pid}: {cmdline}")
            print(
                f"These processes are IGNORED by this test. "
                f"Only tracking {len(self._processes)} test-spawned PIDs.\n"
            )


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


def start_mock_agent_a2a(port: int = 18765, base_dir: Path = None) -> subprocess.Popen:
    """Start the mock agent A2A HTTP server on the specified port.

    Args:
        port: Port number for the A2A server to listen on (default: 18765)
        base_dir: Base directory for the project (defaults to current working directory)

    Returns:
        subprocess.Popen: The mock agent process
    """
    if base_dir is None:
        base_dir = Path.cwd()

    agent_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "mock-agent",
            "--mode",
            "a2a",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
    )
    return agent_proc


def start_and_wait_for_a2a_agent(
    port: int = 18765,
    base_dir: Path = None,
    timeout: float = 10,
    config_file: str = None,
) -> subprocess.Popen:
    """Start A2A mock agent and wait for it to be ready.

    Args:
        port: Port number for the A2A server (default: 18765)
        base_dir: Base directory for the project (defaults to current working directory)
        timeout: Maximum time to wait for agent to be ready (default: 10s)
        config_file: Optional config file for custom responses (e.g., "test-config-responses.json")

    Returns:
        subprocess.Popen: The mock agent process

    Raises:
        AssertionError: If agent fails to start within timeout
    """
    if base_dir is None:
        base_dir = Path.cwd()

    cmd = ["uv", "run", "mock-agent", "--mode", "a2a", "--port", str(port)]
    if config_file:
        cmd.extend(["--config", config_file])

    agent_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
    )

    agent_ready = wait_for_port(
        port, timeout=timeout, health_path="/.well-known/agent-card.json"
    )
    if not agent_ready:
        agent_proc.kill()
        raise AssertionError(f"Mock agent A2A server did not start on port {port}")

    return agent_proc


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


def wait_for_port(
    port: int, timeout: float = 10, health_path: str = "/api/health"
) -> bool:
    """Wait for a port to become available for HTTP requests.

    Args:
        port: Port number to check
        timeout: Maximum time to wait in seconds
        health_path: Health check endpoint path (default: /api/health for scheduler)

    Returns:
        bool: True if port is ready, False if timeout exceeded
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"http://localhost:{port}{health_path}", timeout=1)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.1)
    return False


def start_scheduler(
    port: int, base_dir: Path = None, db_url: str = None, env: dict = None
) -> tuple[subprocess.Popen, str | None]:
    """Start the scheduler binary with specified configuration.

    Args:
        port: Port number for scheduler to listen on
        base_dir: Base directory for the project (defaults to test file parent directory)
        db_url: Database URL (SQLite or PostgreSQL). Creates temp SQLite if None.
        env: Additional environment variables to pass to scheduler

    Returns:
        tuple: (scheduler_process, temp_db_path)
            temp_db_path is None if db_url was provided (caller handles cleanup)

    Raises:
        RuntimeError: If scheduler fails to start within timeout
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Create temp database only if no db_url provided
    temp_db_path = None
    if db_url is None:
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        temp_db_path = temp_db.name
        db_url = f"sqlite:{temp_db_path}?mode=rwc"

    # Prepare environment
    scheduler_env = os.environ.copy()
    if env:
        scheduler_env.update(env)

    scheduler_process = subprocess.Popen(
        [
            "./bin/agentbeacon-scheduler",
            "--port",
            str(port),
            "--db-url",
            db_url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=base_dir,
        env=scheduler_env,
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
def scheduler_context(port: int = None, db_url: str = None, env: dict = None):
    """Context manager for scheduler startup and cleanup.

    Args:
        port: Port number (allocates one if None)
        db_url: Database URL (creates temp SQLite if None)
        env: Additional environment variables to pass to scheduler

    Yields:
        dict: Contains 'process', 'url', 'port', 'db_path', 'db_url'
            Note: db_path will be None if db_url was provided
    """
    port_manager = PortManager() if port is None else None
    allocated_port = port_manager.allocate_port() if port_manager else port
    scheduler_process = None
    temp_db_path = None
    wiki_index_dir = None

    try:
        # Create temp directory for wiki search index
        wiki_index_dir = tempfile.mkdtemp(prefix="wiki-index-")

        # Always disable auto-seeding in tests for predictable state
        merged_env = {
            "AGENTBEACON_NO_SEED": "1",
            "AGENTBEACON_WIKI_INDEX_DIR": wiki_index_dir,
        }
        if env:
            merged_env.update(env)

        scheduler_process, temp_db_path = start_scheduler(
            allocated_port, db_url=db_url, env=merged_env
        )
        yield {
            "process": scheduler_process,
            "url": f"http://localhost:{allocated_port}",
            "port": allocated_port,
            "db_path": temp_db_path,
            "db_url": db_url if db_url else f"sqlite:{temp_db_path}?mode=rwc",
        }
    finally:
        # Cleanup
        if scheduler_process:
            cleanup_processes([scheduler_process])
        if temp_db_path:
            cleanup_files([temp_db_path])
        if wiki_index_dir:
            shutil.rmtree(wiki_index_dir, ignore_errors=True)
        if port_manager:
            port_manager.release_port(allocated_port)


def start_worker(
    orchestrator_url: str,
    interval: str = "1s",
    base_dir: Path = None,
) -> subprocess.Popen:
    """Start the worker binary with specified configuration.

    Args:
        orchestrator_url: URL of the scheduler to connect to
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

    worker_env = os.environ.copy()

    cmd = [
        "./bin/agentbeacon-worker",
        "--scheduler-url",
        orchestrator_url,
        "--interval",
        interval,
    ]

    worker_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
        env=worker_env,
    )

    return worker_process


def start_worker_with_retry_config(
    scheduler_url: str,
    startup_attempts: int = 3,
    reconnect_attempts: int = 5,
    retry_delay_ms: int = 100,
    interval: str = "1s",
    base_dir: Path = None,
) -> subprocess.Popen:
    """Start worker with custom retry configuration for fast tests.

    Args:
        scheduler_url: URL of the scheduler to connect to
        startup_attempts: Maximum retry attempts during startup (default: 3)
        reconnect_attempts: Maximum retry attempts after connection (default: 5)
        retry_delay_ms: Delay between retries in milliseconds (default: 100)
        interval: Sync interval (default: "1s")
        base_dir: Base directory for the project

    Returns:
        subprocess.Popen: The worker process

    Note:
        Fast retry config allows tests to run quickly while validating retry behavior.
        Production defaults: 10 startup / 60 reconnect / 500ms delay.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Copy current environment for pytest context
    worker_env = os.environ.copy()

    worker_process = subprocess.Popen(
        [
            "./bin/agentbeacon-worker",
            "--scheduler-url",
            scheduler_url,
            "--interval",
            interval,
            "--startup-max-attempts",
            str(startup_attempts),
            "--reconnect-max-attempts",
            str(reconnect_attempts),
            "--retry-delay",
            f"{retry_delay_ms}ms",
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
    from agentbeacon.mock_agent.file_logger import parse_agent_entry

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


def register_workflow(
    scheduler_url: str, workflow_yaml: str, namespace: str = "default"
) -> str:
    """Register a workflow and return its reference.

    Args:
        scheduler_url: Base URL of the scheduler (e.g., "http://localhost:19456")
        workflow_yaml: YAML content of the workflow to register
        namespace: Workflow namespace (default: "default")

    Returns:
        str: Workflow reference (e.g., "namespace/name:version")

    Raises:
        AssertionError: If registration fails or response is invalid
    """

    # Parse YAML to extract name
    workflow_data = yaml.safe_load(workflow_yaml)
    name = workflow_data.get("name", "unnamed")

    # Generate UUID-based version
    version = str(uuid.uuid4())

    # Call Rust scheduler registry endpoint
    response = requests.post(
        f"{scheduler_url}/api/registry/workflows",
        json={
            "namespace": namespace,
            "name": name,
            "version": version,
            "isLatest": True,
            "workflowYaml": workflow_yaml,
        },
        timeout=10,
    )
    assert response.status_code == 201, f"Workflow registration failed: {response.text}"

    data = response.json()
    # Construct ref from response: "namespace/name:version"
    workflow_registry_id = data.get("workflowRegistryId") or f"{namespace}/{name}"
    version_from_response = data.get("version", version)
    return f"{workflow_registry_id}:{version_from_response}"


def get_a2a_endpoint(scheduler_url: str) -> str:
    """Discover A2A endpoint from agent card.

    Args:
        scheduler_url: Base URL of the scheduler (e.g., "http://localhost:19456")

    Returns:
        str: A2A endpoint URL from agent card

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

    return agent_card["url"]


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
    # Per A2A spec: contextId is optional field inside Message, not in params
    message = {
        "role": "user",
        "parts": [{"kind": "data", "data": {"data": {"workflowRef": workflow_ref}}}],
        "messageId": str(uuid.uuid4()),
        "kind": "message",
        "contextId": context_id,
    }

    # JSON-RPC request
    jsonrpc_request = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {"message": message},
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
            "params": {"executionId": task_id},
            "id": str(uuid.uuid4()),
        }

        response = requests.post(a2a_endpoint, json=jsonrpc_request, timeout=5)
        assert response.status_code == 200, f"Task status poll failed: {response.text}"

        data = response.json()
        assert "result" in data, f"Task status response should have result: {data}"

        result = data["result"]
        task_status = result.get("status", "unknown")

        # Debug: Print status changes
        if task_status != last_state:
            print(
                f"DEBUG: Task {task_id} status changed: {last_state} -> {task_status}"
            )
            last_state = task_status

        if task_status in ["completed", "failed", "cancelled"]:
            return result

        time.sleep(1)  # Poll every second

    pytest.fail(
        f"Task {task_id} did not complete within {timeout} seconds. Last status: {last_state}"
    )


def start_orchestrator(
    port: int,
    workers: int = 2,
    db_url: str = None,
    base_dir: Path = None,
    worker_poll_interval: str = None,
) -> subprocess.Popen:
    """Start the orchestrator binary with specified configuration.

    Args:
        port: Port number for scheduler to listen on
        workers: Number of worker processes to spawn
        db_url: Complete database URL (creates temp SQLite URL if None)
        base_dir: Base directory for the project (defaults to test file parent directory)
        worker_poll_interval: Worker sync polling interval (e.g., '1s', '500ms'). If None, workers use default (5s)

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

    cmd = [
        "./bin/agentbeacon",
        "--workers",
        str(workers),
        "--scheduler-port",
        str(port),
    ]

    if worker_poll_interval is not None:
        cmd.extend(["--worker-poll-interval", worker_poll_interval])

    orchestrator_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=base_dir,
    )
    return orchestrator_proc


@contextmanager
def orchestrator_context(
    workers: int = 2,
    db_url: str = None,
    port: int = None,
    test_name: str = None,
    worker_poll_interval: str = None,
):
    """Context manager for orchestrator with PID-tracked process management.

    Provides complete lifecycle management for orchestrator tests:
    - Allocates unique port if not provided
    - Starts orchestrator process
    - Discovers and tracks scheduler + worker child PIDs
    - Waits for system readiness
    - Cleans up all tracked processes on exit
    - Logs external processes for debugging

    Args:
        workers: Number of worker processes (default: 2)
        db_url: Database URL (creates temp SQLite if None)
        port: Port number (allocates one if None)
        test_name: Test name for debugging (auto-detected if None)
        worker_poll_interval: Worker sync polling interval (e.g., '1s', '500ms'). If None, workers use default (5s)

    Yields:
        dict: Contains orchestrator info and PID tracker:
            {
                'orchestrator': subprocess.Popen,
                'orchestrator_pid': int,
                'port': int,
                'url': str,
                'tracker': ProcessTracker,
                'scheduler_pids': List[int],
                'worker_pids': List[int],
                'db_url': str,
                'temp_db_path': str or None
            }

    Example:
        with orchestrator_context(workers=2) as orch:
            # Assert using tracker instead of global scanning
            orch['tracker'].assert_exact_count("scheduler", 1)
            orch['tracker'].assert_exact_count("worker", 2)

            # Make API calls
            response = requests.get(f"{orch['url']}/api/health")

            # Processes cleaned up automatically on exit
    """
    if test_name is None:
        test_name = get_current_test_name()

    # Allocate port if not provided
    port_manager = PortManager() if port is None else None
    allocated_port = port_manager.allocate_scheduler_port() if port_manager else port

    # Create temp database if not provided
    temp_db_path = None
    if db_url is None:
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        temp_db_path = temp_db.name
        db_url = f"sqlite:{temp_db_path}?mode=rwc"

    # Create process tracker
    tracker = ProcessTracker(test_name)

    orchestrator_proc = None
    base_dir = Path(__file__).parent.parent

    try:
        # Start orchestrator
        orchestrator_proc = start_orchestrator(
            allocated_port,
            workers=workers,
            db_url=db_url,
            base_dir=base_dir,
            worker_poll_interval=worker_poll_interval,
        )

        # Register orchestrator PID
        orchestrator_pid = tracker.register_process(
            orchestrator_proc,
            "orchestrator",
            {"port": allocated_port, "workers": workers},
        )

        # Wait for scheduler to be ready (orchestrator spawns it)
        if not wait_for_port(allocated_port, timeout=15):
            raise RuntimeError(
                f"Orchestrator system did not start on port {allocated_port} within 15 seconds"
            )

        # Give orchestrator time to spawn all workers
        time.sleep(2)

        # Discover and register child PIDs (scheduler + workers)
        children = tracker.discover_orchestrator_children(orchestrator_pid, timeout=5)
        scheduler_pids = children.get("scheduler", [])
        worker_pids = children.get("workers", [])

        # Log any external processes (for debugging)
        tracker.log_external_processes()

        # Yield control to test
        yield {
            "orchestrator": orchestrator_proc,
            "orchestrator_pid": orchestrator_pid,
            "port": allocated_port,
            "url": f"http://localhost:{allocated_port}",
            "tracker": tracker,
            "scheduler_pids": scheduler_pids,
            "worker_pids": worker_pids,
            "db_url": db_url,
            "temp_db_path": temp_db_path,
        }

    finally:
        # Cleanup all tracked processes
        if tracker:
            tracker.cleanup_all()

        # Cleanup temporary database file
        if temp_db_path:
            cleanup_files([temp_db_path])

        # Release port
        if port_manager:
            port_manager.release_port(allocated_port)


class _PgConnWrapper:
    """Wraps psycopg2 connection to match sqlite3 API conventions.

    Translates ? placeholders to %s so tests can use a single SQL dialect.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


@contextmanager
def db_conn(db_url):
    """Connect to SQLite or PostgreSQL based on URL scheme.

    Yields a connection object with .execute(), .commit(), .close().
    For PostgreSQL, ? placeholders are translated to %s automatically.
    """
    if db_url.startswith("sqlite:"):
        path = db_url.split("sqlite:")[1].split("?")[0]
        conn = sqlite3.connect(path)
        try:
            yield conn
        finally:
            conn.close()
    elif db_url.startswith("postgres"):
        raw_conn = psycopg2.connect(db_url)
        try:
            yield _PgConnWrapper(raw_conn)
        finally:
            raw_conn.close()
    else:
        raise ValueError(f"Unsupported db_url scheme: {db_url}")


def _ensure_driver(conn, agent_type):
    """Return driver_id for agent_type, creating driver if needed."""
    row = conn.execute(
        "SELECT id FROM drivers WHERE platform = ?", (agent_type,)
    ).fetchone()
    if row:
        return row[0]
    driver_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO drivers (id, name, platform, config) VALUES (?, ?, ?, '{}')",
        (driver_id, agent_type, agent_type),
    )
    return driver_id


def seed_test_driver(
    db_url: str,
    name: str = "claude_sdk",
    platform: str = "claude_sdk",
    driver_id: str = None,
) -> str:
    """Insert a test driver directly into the database.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Driver name
        platform: Driver platform
        driver_id: Driver ID (generated UUID if None)

    Returns:
        str: Driver ID
    """
    if driver_id is None:
        driver_id = str(uuid.uuid4())

    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO drivers (id, name, platform, config) VALUES (?, ?, ?, '{}')",
            (driver_id, name, platform),
        )
        conn.commit()

    return driver_id


def seed_test_agent(
    db_url: str,
    name: str = "test-agent",
    agent_type: str = "claude_sdk",
    agent_id: str = None,
    enabled: bool = True,
) -> str:
    """Insert a test agent directly into the database.

    Auto-creates driver for agent_type if one doesn't exist.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Agent name
        agent_type: Agent type (claude_sdk, codex_sdk, acp, etc.)
        agent_id: Agent ID (generated UUID if None)
        enabled: Whether agent is enabled

    Returns:
        str: Agent ID
    """
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, agent_type)
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled) VALUES (?, ?, ?, ?, '{}', ?)",
            (agent_id, name, agent_type, driver_id, enabled),
        )
        conn.commit()

    return agent_id


def seed_acp_mock_agent(
    db_url: str,
    name: str = "acp-mock",
    agent_id: str = None,
) -> str:
    """Insert an ACP mock agent into the database with appropriate config.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Agent name
        agent_id: Agent ID (generated UUID if None)

    Returns:
        str: Agent ID
    """
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    config = json.dumps(
        {
            "command": "uv",
            "args": ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", "acp"],
            "timeout": 30,
        }
    )

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, "acp")
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled) VALUES (?, ?, 'acp', ?, ?, ?)",
            (agent_id, name, driver_id, config, True),
        )
        conn.commit()

    return agent_id


def seed_acp_scenario_agent(
    db_url: str,
    name: str,
    scenario: str,
    delegate_to: str = None,
    delegate_count: int = None,
    agent_id: str = None,
) -> str:
    """Insert an ACP mock agent with a specific scenario into the database.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Agent name
        scenario: Scenario name (delegate, end-turn, delegate-ask, delegate-multi, delegate-release)
        delegate_to: Child agent name for delegation scenarios
        delegate_count: Number of children for delegate-multi
        agent_id: Agent ID (generated UUID if None)

    Returns:
        str: Agent ID
    """
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    args = [
        "run",
        "python",
        "-m",
        "agentbeacon.mock_agent",
        "--mode",
        "acp",
        "--scenario",
        scenario,
    ]
    if delegate_to:
        args.extend(["--delegate-to", delegate_to])
    if delegate_count is not None:
        args.extend(["--delegate-count", str(delegate_count)])

    config = json.dumps({"command": "uv", "args": args, "timeout": 60})

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, "acp")
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled) VALUES (?, ?, 'acp', ?, ?, ?)",
            (agent_id, name, driver_id, config, True),
        )
        conn.commit()

    return agent_id


def create_execution_via_api(
    scheduler_url: str,
    agent_id: str,
    prompt: str,
    title: str = None,
    cwd: str = None,
    project_id: str = None,
    branch: str = None,
    context_id: str = None,
) -> tuple:
    """POST /api/executions, return (execution_id, session_id).

    Args:
        scheduler_url: Base URL of the scheduler
        agent_id: Agent ID to assign the execution to
        prompt: User prompt text
        title: Optional execution title
        cwd: Working directory (defaults to tempfile.gettempdir())
        project_id: Optional project ID
        branch: Optional git branch name
        context_id: Optional context ID

    Returns:
        tuple: (execution_id, session_id)
    """
    if cwd is None and project_id is None:
        cwd = tempfile.gettempdir()

    payload = {"agent_id": agent_id, "prompt": prompt}
    if title is not None:
        payload["title"] = title
    if cwd is not None:
        payload["cwd"] = cwd
    if project_id is not None:
        payload["project_id"] = project_id
    if branch is not None:
        payload["branch"] = branch
    if context_id is not None:
        payload["context_id"] = context_id

    resp = httpx.post(f"{scheduler_url}/api/executions", json=payload, timeout=5)
    assert resp.status_code == 201, (
        f"create execution failed: {resp.status_code} {resp.text}"
    )
    data = resp.json()
    return data["execution"]["id"], data["session_id"]


def mcp_call(
    scheduler_url: str,
    session_id: str,
    method: str,
    params: dict = None,
    rpc_id: int = 1,
) -> dict:
    """POST /mcp with Bearer auth, return parsed JSON-RPC response.

    Args:
        scheduler_url: Base URL of the scheduler
        session_id: Session ID used as Bearer token
        method: JSON-RPC method name
        params: Optional method parameters
        rpc_id: JSON-RPC request ID

    Returns:
        dict: Parsed JSON-RPC response body
    """
    body = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
    if params is not None:
        body["params"] = params

    resp = httpx.post(
        f"{scheduler_url}/mcp",
        json=body,
        headers={
            "Authorization": f"Bearer {session_id}",
            "MCP-Protocol-Version": "2025-11-25",
            "Accept": "application/json, text/event-stream",
        },
        timeout=5,
    )
    if resp.text:
        return resp.json()
    return {}


def mcp_raw(
    scheduler_url: str, session_id: str, method: str, params: dict = None, rpc_id=1
) -> httpx.Response:
    """POST /mcp with Bearer auth, return raw httpx.Response.

    Args:
        scheduler_url: Base URL of the scheduler
        session_id: Session ID used as Bearer token
        method: JSON-RPC method name
        params: Optional method parameters
        rpc_id: JSON-RPC request ID (None for notifications)

    Returns:
        httpx.Response: Raw HTTP response
    """
    body = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        body["id"] = rpc_id
    if params is not None:
        body["params"] = params

    return httpx.post(
        f"{scheduler_url}/mcp",
        json=body,
        headers={
            "Authorization": f"Bearer {session_id}",
            "MCP-Protocol-Version": "2025-11-25",
            "Accept": "application/json, text/event-stream",
        },
        timeout=5,
    )


def mcp_tools_list(scheduler_url: str, session_id: str) -> list:
    """Call tools/list, return list of tool name strings.

    Args:
        scheduler_url: Base URL of the scheduler
        session_id: Session ID used as Bearer token

    Returns:
        list: Tool name strings
    """
    resp = mcp_call(scheduler_url, session_id, "tools/list")
    tools = resp.get("result", {}).get("tools", [])
    return [t["name"] for t in tools]


def mcp_tools_call(
    scheduler_url: str, session_id: str, tool_name: str, arguments: dict
) -> dict:
    """Call tools/call, return result content.

    Args:
        scheduler_url: Base URL of the scheduler
        session_id: Session ID used as Bearer token
        tool_name: Name of the tool to call
        arguments: Tool arguments

    Returns:
        dict: The result object from the JSON-RPC response
    """
    resp = mcp_call(
        scheduler_url,
        session_id,
        "tools/call",
        params={"name": tool_name, "arguments": arguments},
    )
    return resp.get("result", {})


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


def seed_project(
    db_url: str,
    name: str = "test-project",
    path: str = None,
    project_id: str = None,
) -> str:
    """Insert a test project directly into the database.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Project name
        path: Project path (defaults to a temp directory)
        project_id: Project ID (generated UUID if None)

    Returns:
        str: Project ID
    """
    if project_id is None:
        project_id = str(uuid.uuid4())
    if path is None:
        path = tempfile.gettempdir()

    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, path, settings) VALUES (?, ?, ?, '{}')",
            (project_id, name, path),
        )
        conn.commit()

    return project_id


def create_project_via_api(scheduler_url: str, name: str, path: str = None) -> dict:
    """POST /api/projects, return response data.

    Args:
        scheduler_url: Base URL of the scheduler
        name: Project name
        path: Project path (defaults to a temp directory)

    Returns:
        dict: Project response data
    """
    if path is None:
        path = tempfile.gettempdir()

    resp = httpx.post(
        f"{scheduler_url}/api/projects",
        json={"name": name, "path": path},
        timeout=5,
    )
    assert resp.status_code == 201, (
        f"create project failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def ensure_driver_via_api(scheduler_url: str, platform: str = "acp") -> str:
    """Find or create a driver for the given platform, return driver_id."""
    resp = httpx.get(f"{scheduler_url}/api/drivers", timeout=5)
    assert resp.status_code == 200
    for driver in resp.json():
        if driver["platform"] == platform:
            return driver["id"]

    resp = httpx.post(
        f"{scheduler_url}/api/drivers",
        json={"name": platform, "platform": platform},
        timeout=5,
    )
    assert resp.status_code == 201, (
        f"create driver failed: {resp.status_code} {resp.text}"
    )
    return resp.json()["id"]


def create_agent_via_api(
    scheduler_url: str,
    name: str,
    driver_id: str = None,
    config: dict = None,
    description: str = None,
) -> dict:
    """POST /api/agents, return response data.

    Args:
        scheduler_url: Base URL of the scheduler
        name: Agent name
        driver_id: Driver ID (auto-resolved from 'acp' platform if None)
        config: Agent config (defaults to minimal valid config)
        description: Optional description

    Returns:
        dict: Agent response data
    """
    if config is None:
        config = {"command": "echo", "args": ["test"], "timeout": 60}

    if driver_id is None:
        driver_id = ensure_driver_via_api(scheduler_url)

    payload = {"name": name, "driver_id": driver_id, "config": config}
    if description is not None:
        payload["description"] = description

    resp = httpx.post(
        f"{scheduler_url}/api/agents",
        json=payload,
        timeout=5,
    )
    assert resp.status_code == 201, (
        f"create agent failed: {resp.status_code} {resp.text}"
    )
    return resp.json()
