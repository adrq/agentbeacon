# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Common utilities for test process and file cleanup.

These helpers centralize robust termination of subprocesses and cleanup of
temporary files to reduce duplication and flakiness across tests.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import socket
import subprocess
import tempfile
import termios
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
import hashlib
import itertools
import json
import secrets
from urllib.parse import urlparse


class PortManager:
    """Thread-safe port allocation manager for test isolation.

    Uses OS-assigned ephemeral ports (bind to port 0) to eliminate
    cross-process TOCTOU races that occur when scanning a fixed range.
    The OS picks from ~28K ephemeral ports, making collisions near-impossible.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._allocated: Set[int] = set()

    def allocate_port(self) -> int:
        """Allocate a free port using OS ephemeral assignment."""
        with self._lock:
            for _ in range(10):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                sock.close()
                if port not in self._allocated:
                    self._allocated.add(port)
                    return port
            raise RuntimeError("Failed to allocate unique port after 10 attempts")

    def allocate_scheduler_port(self) -> int:
        return self.allocate_port()

    def allocate_agent_port(self) -> int:
        return self.allocate_port()

    def release_port(self, port: int) -> None:
        with self._lock:
            self._allocated.discard(port)

    @contextmanager
    def port_context(self, port_type: str = "scheduler"):
        """Context manager for automatic port allocation and cleanup."""
        port = self.allocate_port()
        try:
            yield port
        finally:
            self.release_port(port)


class ProcessTracker:
    """Track and manage test-spawned processes for isolation from external instances.

    Provides PID-based process tracking to avoid conflicts when AgentBeacon
    is running separately outside of tests.

    Example:
        tracker = ProcessTracker()
        orchestrator_proc = start_orchestrator(port=19456)
        tracker.register_process(orchestrator_proc, "orchestrator", {"port": 19456})

        # Later: count only our processes
        count = tracker.count_alive("worker")

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
        self, orchestrator_pid: int, timeout: float = 5.0, expected_workers: int = 0
    ) -> Dict[str, List[int]]:
        """Discover and register worker child processes spawned by agentbeacon.

        Polls for worker processes that are children of the agentbeacon process.

        Args:
            orchestrator_pid: PID of agentbeacon parent process
            timeout: Maximum time to wait for children to appear
            expected_workers: Number of workers expected (exit early once found)

        Returns:
            Dict with key "workers" containing discovered PIDs
        """
        start_time = time.time()
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

                if expected_workers > 0 and len(found_workers) >= expected_workers:
                    time.sleep(0.2)
                    break

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            time.sleep(0.1)

        return {
            "workers": found_workers,
        }

    def log_external_processes(self, process_type: str = None) -> None:
        """Log any external processes matching our patterns (for debugging).

        Args:
            process_type: Optional type to check ("scheduler", "worker", etc.)
        """
        patterns = {
            "agentbeacon": "agentbeacon",
            "worker": "agentbeacon-worker",
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


# Max bytes of a captured scheduler/orchestrator log to surface in a
# startup-failure diagnostic. Under the high-volume logging this redirection
# guards against the file can grow large, so we only read the tail.
_LOG_TAIL_BYTES = 256 * 1024


def _read_log_tail(log_path: str, max_bytes: int = _LOG_TAIL_BYTES) -> str:
    """Return the last ``max_bytes`` of a captured process log for diagnostics."""
    try:
        size = os.path.getsize(log_path)
        with open(log_path, errors="replace") as f:
            truncated = size > max_bytes
            if truncated:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError:
        return "<process log unavailable>"
    if truncated:
        return f"... [truncated to last {max_bytes} bytes]\n{data}"
    return data


class PortInUseError(RuntimeError):
    pass


_STARTUP_ATTEMPTS = 3

_LOOPBACK = "127.0.0.1"
# Hosts the harness reaches its schedulers by; credentials go to no other.
_INJECTION_HOSTS = (_LOOPBACK, "localhost")


def _listens_on(proc: subprocess.Popen, port: int) -> bool:
    try:
        root = psutil.Process(proc.pid)
        candidates = [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        return False
    for candidate in candidates:
        try:
            for conn in candidate.net_connections(kind="inet"):
                if (
                    conn.status == psutil.CONN_LISTEN
                    and conn.family == socket.AF_INET
                    and conn.laddr
                    and conn.laddr.ip in ("0.0.0.0", _LOOPBACK)
                    and conn.laddr.port == port
                ):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _wait_until_serving(
    proc: subprocess.Popen, port: int, timeout: float, health_path: str = "/api/health"
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            if "Address already in use" in _read_log_tail(proc.log_path):
                raise PortInUseError(
                    f"port {port} was taken before the server bound it"
                )
            return False
        if _listens_on(proc, port):
            try:
                url = f"http://{_LOOPBACK}:{port}{health_path}"
                if requests.get(url, timeout=1).status_code == 200:
                    return True
            except requests.RequestException:
                pass
        time.sleep(0.1)
    return False


def _close_process_log(proc: subprocess.Popen, remove: bool = True) -> None:
    log_file = getattr(proc, "log_file", None)
    if log_file is not None:
        try:
            log_file.close()
        except OSError:
            pass
    log_path = getattr(proc, "log_path", None)
    if remove and log_path:
        try:
            os.unlink(log_path)
        except OSError:
            pass


TEST_USERNAME = "test-admin"
# Generated once per test process; setup, login and restarts all use it.
TEST_PASSWORD = secrets.token_urlsafe(24)

# A request carrying this header is sent without injected credentials;
# the header itself is removed before the request leaves the client.
ANONYMOUS_HEADER = "x-test-anonymous"

_SCHEDULER_AUTH: Dict[int, Dict[str, Any]] = {}
_WORKER_TOKENS: Dict[tuple, str] = {}
_WORKER_LABELS: Dict[tuple, str] = {}
_AGENT_TOKENS: Dict[tuple, str] = {}
_GENERATIONS = itertools.count(1)
_AUTH_LOCK = threading.RLock()


def bootstrap_user(db_url: str, base_dir: Path = None, env: dict = None) -> None:
    """Create the test user in `db_url` with `agentbeacon --setup-user`."""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    setup_env = os.environ.copy()
    if env:
        setup_env.update(env)
    setup_env["AGENTBEACON_USERNAME"] = TEST_USERNAME
    setup_env["AGENTBEACON_PASSWORD"] = TEST_PASSWORD
    result = subprocess.run(
        ["./bin/agentbeacon", "--setup-user", "--db-url", db_url],
        cwd=base_dir,
        env=setup_env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"user setup failed ({result.returncode}): {result.stdout}{result.stderr}"
        )


def _set_controlling_tty():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def run_pty(argv, cwd, env, replies=(), timeout=90):
    """Run `argv` with a terminal as stdin, stdout and controlling tty, answering prompts in order."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        start_new_session=True,
        preexec_fn=_set_controlling_tty,
    )
    os.close(slave)
    pending = list(replies)
    output = b""
    answered = 0
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                if proc.poll() is not None:
                    break
                continue
            output += chunk
            if pending:
                prompt = pending[0][0].encode()
                found = output.find(prompt, answered)
                if found != -1:
                    answered = found + len(prompt)
                    os.write(master, pending.pop(0)[1].encode())
        else:
            proc.kill()
        proc.wait(timeout=10)
        stderr = proc.stderr.read().decode()
    finally:
        proc.stderr.close()
        os.close(master)
    return (
        proc.returncode,
        output.decode(errors="replace").replace("\r\n", "\n"),
        stderr,
    )


def db_location(db_url: str) -> str:
    """How `agentbeacon` names the database at `db_url` in its output."""
    if db_url.startswith("sqlite:"):
        path = db_url[len("sqlite:") :].split("?", 1)[0]
        if path.startswith("//"):
            path = path[2:]
        return f"SQLite at {os.path.abspath(path)}"
    parsed = urlparse(db_url)
    database = parsed.path.lstrip("/") or parsed.username
    return (
        f'PostgreSQL database "{database}" on {parsed.hostname}:{parsed.port or 5432}'
        f" as {parsed.username}"
    )


def _port_of(url: str) -> int:
    return urlparse(url).port


def register_scheduler(port: int, db_url: str) -> str:
    """Log in to the scheduler on `port` and remember its human token."""
    resp = httpx.post(
        f"http://{_LOOPBACK}:{port}/api/v1/auth/login",
        json={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "label": "pytest",
            "delivery": "bearer",
        },
        headers={ANONYMOUS_HEADER: "1"},
        timeout=30,
    )
    if resp.status_code != 201:
        raise RuntimeError(f"login failed: {resp.status_code} {resp.text}")
    token = resp.json()["token"]
    _SCHEDULER_AUTH[port] = {
        "token": token,
        "db_url": db_url,
        "generation": next(_GENERATIONS),
    }
    return token


def unregister_scheduler(port: int) -> None:
    """Forget the credentials held for the scheduler on `port`."""
    _SCHEDULER_AUTH.pop(port, None)
    for key in [k for k in _WORKER_TOKENS if k[0] == port]:
        del _WORKER_TOKENS[key]
    for key in [k for k in _WORKER_LABELS if k[0] == port]:
        del _WORKER_LABELS[key]
    for key in [k for k in _AGENT_TOKENS if k[0] == port]:
        del _AGENT_TOKENS[key]


def _scheduler_info(url: str = None) -> tuple:
    if url is None:
        if len(_SCHEDULER_AUTH) != 1:
            raise RuntimeError(
                f"pass the scheduler url: {len(_SCHEDULER_AUTH)} schedulers are running"
            )
        port = next(iter(_SCHEDULER_AUTH))
    else:
        port = _port_of(url)
    if port not in _SCHEDULER_AUTH:
        raise RuntimeError(f"no test scheduler is registered on port {port}")
    return port, _SCHEDULER_AUTH[port]


def human_token(url: str) -> str:
    """The harness's human bearer token for the scheduler at `url`."""
    return _scheduler_info(url)[1]["token"]


def human_headers(url: str) -> dict:
    """An Authorization header carrying the human token."""
    return {"Authorization": f"Bearer {human_token(url)}"}


UNAUTHORIZED_DETAIL = "a valid credential is required"
FORBIDDEN_DETAIL = "this credential is not permitted to do this"
LOGIN_FAILED_DETAIL = "invalid username or password"


def assert_problem(resp, status: int, code: str, detail: str) -> None:
    """Assert `resp` is a problem document with this status and code, whose
    detail contains `detail`."""
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.headers
    )
    body = resp.json()
    assert body["code"] == code, body
    assert detail in body["detail"], body


def assert_unauthorized(resp, detail: str = UNAUTHORIZED_DETAIL) -> None:
    """Assert `resp` is the 401 for a missing or unusable credential."""
    assert_problem(resp, 401, "auth.unauthorized", detail)
    assert resp.headers["www-authenticate"] == "Bearer"


def assert_forbidden(resp) -> None:
    """Assert `resp` is the 403 for a credential that may not do this."""
    assert_problem(resp, 403, "auth.forbidden", FORBIDDEN_DETAIL)


def anonymous_headers(extra: dict = None) -> dict:
    """Headers that send a request with no injected credential."""
    headers = {ANONYMOUS_HEADER: "1"}
    if extra:
        headers.update(extra)
    return headers


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def agent_token(url: str, session_id: str) -> str:
    """A session credential for `session_id`, written to the test database.

    The row replaces any existing credential of that session, as an assign does.
    A token handed out earlier is reused while its row is still the session's.
    """
    port, info = _scheduler_info(url)
    with _AUTH_LOCK:
        return _agent_token_locked(port, info, session_id)


def _agent_token_locked(port: int, info: dict, session_id: str) -> str:
    cached = _AGENT_TOKENS.get((port, session_id))
    with db_conn(info["db_url"]) as conn:
        if cached is not None:
            row = conn.execute(
                "SELECT 1 FROM credentials WHERE token_hash = ? "
                "AND principal_type = 'session' AND principal_id = ?",
                (_token_hash(cached), session_id),
            ).fetchone()
            if row is not None:
                return cached
        token = secrets.token_hex(32)
        conn.execute(
            "DELETE FROM credentials WHERE principal_type = 'session' AND principal_id = ?",
            (session_id,),
        )
        conn.execute(
            "INSERT INTO credentials (token_hash, principal_type, principal_id) "
            "VALUES (?, 'session', ?)",
            (_token_hash(token), session_id),
        )
        conn.commit()
    _AGENT_TOKENS[(port, session_id)] = token
    return token


def agent_headers(url: str, session_id: str, extra: dict = None) -> dict:
    """An Authorization header carrying `session_id`'s credential."""
    headers = {"Authorization": f"Bearer {agent_token(url, session_id)}"}
    if extra:
        headers.update(extra)
    return headers


def admit_worker(url: str = None) -> str:
    """Admit an externally supervised worker; returns its worker id.

    The worker's token is kept by the harness and sent with requests that name
    the worker.
    """
    port, info = _scheduler_info(url)
    resp = httpx.post(
        f"http://{_LOOPBACK}:{port}/api/v1/workers",
        headers={"Authorization": f"Bearer {info['token']}"},
        timeout=10,
    )
    if resp.status_code != 201:
        raise RuntimeError(f"worker admission failed: {resp.status_code} {resp.text}")
    body = resp.json()
    _WORKER_TOKENS[(port, body["worker_id"])] = body["token"]
    return body["worker_id"]


def admit_worker_with_token(url: str = None) -> tuple:
    """Admit a worker and return `(worker_id, token)`."""
    port, _ = _scheduler_info(url)
    worker_id = admit_worker(url)
    return worker_id, _WORKER_TOKENS[(port, worker_id)]


def worker_token(url: str, worker_id: str) -> str | None:
    """The token the harness holds for an admitted worker."""
    return _WORKER_TOKENS.get((_port_of(url), worker_id))


def wid(label: str, url: str = None) -> str:
    """The admitted worker id standing for `label` on this scheduler.

    The first use admits a worker; later uses on the same scheduler return it.
    """
    port, info = _scheduler_info(url)
    key = (port, info["generation"], label)
    with _AUTH_LOCK:
        if key not in _WORKER_LABELS:
            _WORKER_LABELS[key] = admit_worker(f"http://{_LOOPBACK}:{port}")
        return _WORKER_LABELS[key]


def fresh_wid(label: str, url: str = None) -> str:
    """Admit a new worker for `label`, replacing the one it stood for."""
    port, info = _scheduler_info(url)
    worker_id = admit_worker(f"http://{_LOOPBACK}:{port}")
    _WORKER_LABELS[(port, info["generation"], label)] = worker_id
    return worker_id


DUMMY_WORKER_TOKEN = "test-bootstrap-token"


def worker_credential(url: str) -> tuple:
    """`(worker_id, token)` for launching a worker binary against `url`.

    A registered test scheduler admits a worker; any other target (a mock
    scheduler, an unreachable address) gets a fixed dummy credential.
    """
    port = _port_of(url)
    if port in _SCHEDULER_AUTH:
        return admit_worker_with_token(f"http://{_LOOPBACK}:{port}")
    return str(uuid.uuid4()), DUMMY_WORKER_TOKEN


def worker_launch(url: str) -> tuple:
    """`(extra_args, extra_env)` that give a worker binary its credential."""
    worker_id, token = worker_credential(url)
    return ["--worker-id", worker_id], {"AGENTBEACON_WORKER_TOKEN": token}


def _worker_id_in_body(content: bytes) -> str | None:
    try:
        body = json.loads(content or b"{}")
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    value = body.get("worker_id", body.get("workerId"))
    return value if isinstance(value, str) else None


def injected_authorization(url: str, path: str, headers, content: bytes) -> str | None:
    """The Authorization value the harness adds to a request, if any.

    Requests to a registered scheduler (plain http, a loopback host the harness
    uses, and the scheduler's port) that carry no Authorization header and no
    cookie get the human token, or on the worker routes the token of the
    admitted worker the body names.
    """
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in _INJECTION_HOSTS:
        return None
    port = parsed.port
    info = _SCHEDULER_AUTH.get(port)
    if info is None:
        return None
    if path in ("/api/worker/sync", "/api/worker/events"):
        worker_id = _worker_id_in_body(content)
        token = _WORKER_TOKENS.get((port, worker_id)) if worker_id else None
        return f"Bearer {token}" if token else None
    return f"Bearer {info['token']}"


def _discard_failed_start(proc: subprocess.Popen, temp_db_path: str = None) -> None:
    children = []
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.Error:
        pass
    _terminate_single_process(proc)
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        _close_process_log(proc)
    except Exception:
        pass
    if temp_db_path:
        try:
            cleanup_files([temp_db_path])
        except Exception:
            pass


def start_scheduler(
    port: int,
    base_dir: Path = None,
    db_url: str = None,
    env: dict = None,
    create_user: bool = True,
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

    # Redirect stdout+stderr to a per-scheduler temp log file rather than an
    # unread subprocess.PIPE: under load a high-volume process can fill an
    # undrained pipe's OS buffer and block on write. A file lets the OS drain
    # output to disk while keeping it readable for diagnostics.
    log_file = tempfile.NamedTemporaryFile(
        mode="w+", suffix=".log", prefix=f"scheduler-{port}-", delete=False
    )
    log_path = log_file.name

    try:
        if create_user:
            bootstrap_user(db_url, base_dir, env)
        scheduler_process = subprocess.Popen(
            [
                "./bin/agentbeacon",
                "--port",
                str(port),
                "--db-url",
                db_url,
                "--max-workers",
                "0",
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=base_dir,
            env=scheduler_env,
        )
    except Exception:
        # Popen failed (e.g. missing binary / bad cwd) — don't leak the log file.
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass
        if temp_db_path:
            cleanup_files([temp_db_path])
        raise
    # Stash the log handle/path on the process so scheduler_context can close
    # and remove it during teardown.
    scheduler_process.log_file = log_file
    scheduler_process.log_path = log_path

    # Wait for scheduler ready
    try:
        if not _wait_until_serving(scheduler_process, port, timeout=15):
            # Surface a bounded tail of the captured output, then clean up.
            captured = _read_log_tail(log_path)
            raise RuntimeError(
                f"Scheduler failed to start within timeout. Output (tail):\n{captured}"
            )
        if create_user:
            register_scheduler(port, db_url)
    except BaseException:
        _discard_failed_start(scheduler_process, temp_db_path)
        raise

    return scheduler_process, temp_db_path


@contextmanager
def scheduler_context(
    port: int = None, db_url: str = None, env: dict = None, create_user: bool = True
):
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
    allocated_port = port
    tried_ports = []
    scheduler_process = None
    temp_db_path = None
    wiki_index_dir = None
    keep_log = False

    try:
        # Create temp directory for wiki search index
        wiki_index_dir = tempfile.mkdtemp(prefix="wiki-index-")

        merged_env = {
            "AGENTBEACON_WIKI_INDEX_DIR": wiki_index_dir,
            "AGENTBEACON_LONG_POLL_TIMEOUT_SECS": "0",
        }
        if env:
            merged_env.update(env)

        attempts = _STARTUP_ATTEMPTS if port_manager else 1
        for attempt in range(attempts):
            if port_manager:
                allocated_port = port_manager.allocate_port()
                tried_ports.append(allocated_port)
            try:
                scheduler_process, temp_db_path = start_scheduler(
                    allocated_port,
                    db_url=db_url,
                    env=merged_env,
                    create_user=create_user,
                )
                break
            except PortInUseError:
                if attempt == attempts - 1:
                    raise
        try:
            yield {
                "process": scheduler_process,
                "url": f"http://{_LOOPBACK}:{allocated_port}",
                "port": allocated_port,
                "db_path": temp_db_path,
                "db_url": db_url if db_url else f"sqlite:{temp_db_path}?mode=rwc",
            }
        except GeneratorExit:
            raise
        except BaseException as error:
            keep_log = True
            error.add_note(f"scheduler log: {scheduler_process.log_path}")
            raise
    finally:
        # Cleanup
        if allocated_port is not None:
            unregister_scheduler(allocated_port)
        if scheduler_process:
            cleanup_processes([scheduler_process])
            _close_process_log(scheduler_process, remove=not keep_log)
        if temp_db_path:
            cleanup_files([temp_db_path])
        if wiki_index_dir:
            shutil.rmtree(wiki_index_dir, ignore_errors=True)
        for tried in tried_ports:
            port_manager.release_port(tried)


def start_worker(
    orchestrator_url: str,
    interval: str = "1s",
    base_dir: Path = None,
    extra_env: dict = None,
) -> subprocess.Popen:
    """Start the worker binary with specified configuration.

    Args:
        orchestrator_url: URL of the scheduler to connect to
        interval: Polling interval (default: "1s")
        base_dir: Base directory for the project (defaults to test file parent directory)
        extra_env: Additional environment variables to set on the worker process

    Returns:
        subprocess.Popen: The worker process

    Note:
        Worker inherits current environment variables including PYTEST_CURRENT_TEST
        for agent logging support.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    launch_args, launch_env = worker_launch(orchestrator_url)
    worker_env = os.environ.copy()
    worker_env.update(launch_env)
    if extra_env:
        worker_env.update(extra_env)

    cmd = [
        "./bin/agentbeacon-worker",
        "--scheduler-url",
        orchestrator_url,
        "--interval",
        interval,
        *launch_args,
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
    extra_env: dict = None,
    extra_args: list = None,
) -> subprocess.Popen:
    """Start worker with custom retry configuration for fast tests.

    Args:
        scheduler_url: URL of the scheduler to connect to
        startup_attempts: Maximum retry attempts during startup (default: 3)
        reconnect_attempts: Maximum retry attempts after connection (default: 5)
        retry_delay_ms: Delay between retries in milliseconds (default: 100)
        interval: Sync interval (default: "1s")
        base_dir: Base directory for the project
        extra_env: Additional environment variables for the worker process
            (inherited by the executor subprocess)
        extra_args: Additional CLI args appended to the worker command line
            (e.g. ["--inactivity-timeout", "1s"])

    Returns:
        subprocess.Popen: The worker process

    Note:
        Fast retry config allows tests to run quickly while validating retry behavior.
        Production defaults: 10 startup / 60 reconnect / 500ms delay.
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Copy current environment for pytest context
    launch_args, launch_env = worker_launch(scheduler_url)
    worker_env = os.environ.copy()
    worker_env.update(launch_env)
    if extra_env:
        worker_env.update(extra_env)

    cmd = [
        "./bin/agentbeacon-worker",
        "--scheduler-url",
        scheduler_url,
        *launch_args,
        "--interval",
        interval,
        "--startup-max-attempts",
        str(startup_attempts),
        "--reconnect-max-attempts",
        str(reconnect_attempts),
        "--retry-delay",
        f"{retry_delay_ms}ms",
    ]
    if extra_args:
        cmd.extend(extra_args)

    worker_process = subprocess.Popen(
        cmd,
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


def start_orchestrator(
    port: int,
    max_workers: int | None = None,
    db_url: str = None,
    base_dir: Path = None,
    worker_poll_interval: str = None,
    idle_timeout: str = None,
    env: dict = None,
    binary: str = "./bin/agentbeacon",
) -> subprocess.Popen:
    """Start the agentbeacon binary with specified configuration.

    Args:
        port: Port number for scheduler to listen on
        max_workers: Maximum auto-spawned workers. None omits flag (unlimited). 0 disables.
        db_url: Complete database URL (creates temp SQLite URL if None)
        base_dir: Base directory for the project (defaults to test file parent directory)
        worker_poll_interval: Worker sync polling interval (e.g., '1s', '500ms'). If None, workers use default (5s)
        idle_timeout: Worker idle timeout (e.g., '300s'). If None, workers use default (5m)

    Returns:
        subprocess.Popen: The agentbeacon process
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    # Create temp database URL if not provided
    if db_url is None:
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db_url = f"sqlite:///{temp_db.name}"

    extra_env = env
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    # Short intervals for tests so dynamic workers react quickly.
    env.setdefault("AGENTBEACON_RECOVERY_GRACE_SECS", "3")
    env.setdefault("AGENTBEACON_LONG_POLL_TIMEOUT_SECS", "5")
    env.setdefault("AGENTBEACON_HEARTBEAT_TIMEOUT_SECS", "10")
    env.setdefault("AGENTBEACON_LIVENESS_INTERVAL_SECS", "10")
    env.setdefault("AGENTBEACON_SPAWN_TICK_SECS", "2")
    if extra_env:
        env.update(extra_env)

    cmd = [
        binary,
        "--port",
        str(port),
    ]

    if max_workers is not None:
        cmd.extend(["--max-workers", str(max_workers)])

    if worker_poll_interval is not None:
        cmd.extend(["--worker-poll-interval", worker_poll_interval])

    if idle_timeout is not None:
        cmd.extend(["--idle-timeout", idle_timeout])

    # Redirect stdout+stderr to a per-orchestrator temp log file rather than an
    # unread subprocess.PIPE — an undrained PIPE fills the OS buffer under load
    # and BLOCKS the process on write. Output stays readable via the log file
    # (see orchestrator_context's "orchestrator_log").
    log_file = tempfile.NamedTemporaryFile(
        mode="w+", suffix=".log", prefix=f"orchestrator-{port}-", delete=False
    )
    log_path = log_file.name

    try:
        bootstrap_user(db_url, base_dir)
        orchestrator_proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=base_dir,
        )
    except Exception:
        # Popen failed (e.g. missing binary / bad cwd) — don't leak the log file.
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass
        raise
    # Stash the log handle/path so orchestrator_context can expose it to tests
    # and close+remove it during teardown.
    orchestrator_proc.log_file = log_file
    orchestrator_proc.log_path = log_path
    return orchestrator_proc


@contextmanager
def orchestrator_context(
    max_workers: int | None = None,
    db_url: str = None,
    port: int = None,
    test_name: str = None,
    worker_poll_interval: str = None,
    idle_timeout: str = None,
    env: dict = None,
    binary: str = "./bin/agentbeacon",
):
    """Context manager for agentbeacon with PID-tracked process management.

    Provides complete lifecycle management for supervisor tests:
    - Allocates unique port if not provided
    - Starts agentbeacon process
    - Discovers and tracks worker child PIDs
    - Waits for system readiness
    - Cleans up all tracked processes on exit
    - Logs external processes for debugging

    Args:
        max_workers: Maximum auto-spawned workers. None = unlimited (default). 0 = disabled.
        db_url: Database URL (creates temp SQLite if None)
        port: Port number (allocates one if None)
        test_name: Test name for debugging (auto-detected if None)
        worker_poll_interval: Worker sync polling interval (e.g., '1s', '500ms'). If None, workers use default (5s)
        idle_timeout: Worker idle timeout (e.g., '300s'). If None, workers use default (5m)

    Yields:
        dict: Contains agentbeacon process info and PID tracker:
            {
                'orchestrator': subprocess.Popen,
                'orchestrator_pid': int,
                'port': int,
                'url': str,
                'tracker': ProcessTracker,
                'worker_pids': List[int],
                'db_url': str,
                'temp_db_path': str or None
            }

    Example:
        with orchestrator_context(max_workers=2) as orch:
            # Assert using tracker
            orch['tracker'].assert_exact_count("worker", 2)

            # Make API calls
            response = requests.get(f"{orch['url']}/api/health")

            # Processes cleaned up automatically on exit
    """
    if test_name is None:
        test_name = get_current_test_name()

    # Allocate port if not provided
    port_manager = PortManager() if port is None else None
    allocated_port = port
    tried_ports = []

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
    keep_log = False

    try:
        attempts = _STARTUP_ATTEMPTS if port_manager else 1
        for attempt in range(attempts):
            if port_manager:
                allocated_port = port_manager.allocate_scheduler_port()
                tried_ports.append(allocated_port)
            # Start orchestrator
            orchestrator_proc = start_orchestrator(
                allocated_port,
                max_workers=max_workers,
                db_url=db_url,
                base_dir=base_dir,
                worker_poll_interval=worker_poll_interval,
                idle_timeout=idle_timeout,
                env=env,
                binary=binary,
            )
            # Wait for scheduler to be ready
            try:
                if not _wait_until_serving(
                    orchestrator_proc, allocated_port, timeout=15
                ):
                    captured = _read_log_tail(orchestrator_proc.log_path)
                    raise RuntimeError(
                        f"AgentBeacon did not start on port {allocated_port} within "
                        f"15 seconds. Output (tail):\n{captured}"
                    )
            except PortInUseError:
                _discard_failed_start(orchestrator_proc)
                orchestrator_proc = None
                if attempt == attempts - 1:
                    raise
                continue
            except BaseException:
                _discard_failed_start(orchestrator_proc)
                orchestrator_proc = None
                raise
            break
        register_scheduler(allocated_port, db_url)

        # Register orchestrator PID
        effective_workers = max_workers if max_workers is not None else 0
        orchestrator_pid = tracker.register_process(
            orchestrator_proc,
            "orchestrator",
            {"port": allocated_port, "workers": effective_workers},
        )

        # Give agentbeacon time to spawn all workers
        time.sleep(2)

        # Discover and register worker child PIDs
        children = tracker.discover_orchestrator_children(
            orchestrator_pid, timeout=5, expected_workers=effective_workers
        )
        worker_pids = children.get("workers", [])

        # Log any external processes (for debugging)
        tracker.log_external_processes()

        # Yield control to test
        try:
            yield {
                "orchestrator": orchestrator_proc,
                "orchestrator_pid": orchestrator_pid,
                "orchestrator_log": orchestrator_proc.log_path,
                "port": allocated_port,
                "url": f"http://{_LOOPBACK}:{allocated_port}",
                "tracker": tracker,
                "worker_pids": worker_pids,
                "db_url": db_url,
                "temp_db_path": temp_db_path,
            }
        except GeneratorExit:
            raise
        except BaseException as error:
            keep_log = True
            error.add_note(f"orchestrator log: {orchestrator_proc.log_path}")
            raise

    finally:
        if allocated_port is not None:
            unregister_scheduler(allocated_port)
        # Cleanup all tracked processes
        if tracker:
            tracker.cleanup_all()

        # Close and remove the captured orchestrator log file (see
        # start_orchestrator).
        if orchestrator_proc is not None:
            _terminate_single_process(orchestrator_proc)
            _close_process_log(orchestrator_proc, remove=not keep_log)

        # Cleanup temporary database file
        if temp_db_path:
            cleanup_files([temp_db_path])

        # Release port
        for tried in tried_ports:
            port_manager.release_port(tried)


class _PgConnWrapper:
    """Wraps psycopg2 connection to match sqlite3 API conventions.

    Translates ? placeholders to %s so tests can use a single SQL dialect.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        # SQLite's INSERT OR IGNORE → PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
        if "INSERT OR IGNORE INTO" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO", 1)
            sql = sql.rstrip(";") + " ON CONFLICT DO NOTHING"
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
    """Return driver_id for agent_type. Raises if not found (drivers come from migration)."""
    row = conn.execute(
        "SELECT id FROM drivers WHERE platform = ?", (agent_type,)
    ).fetchone()
    if row:
        return row[0]
    raise RuntimeError(
        f"Driver not found for platform '{agent_type}'. "
        "Drivers are created by migration 0005."
    )


def seed_test_driver(
    db_url: str,
    name: str = "claude_sdk",
    platform: str = "claude_sdk",
    driver_id: str = None,
) -> str:
    """Return driver_id for platform, creating only if it doesn't exist.

    Drivers for standard platforms (acp, claude_sdk, etc.) are created by
    migration 0005 and will already exist. This function handles both cases.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Driver name
        platform: Driver platform
        driver_id: Driver ID (generated UUID if None)

    Returns:
        str: Driver ID
    """
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT id FROM drivers WHERE platform = ?", (platform,)
        ).fetchone()
        if row:
            return row[0]

        if driver_id is None:
            driver_id = str(uuid.uuid4())
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
    system_prompt: str = None,
    config: str = None,
) -> str:
    """Insert a test agent directly into the database.

    Auto-creates driver for agent_type if one doesn't exist.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Agent name
        agent_type: Agent type (claude_sdk, acp, etc.)
        agent_id: Agent ID (generated UUID if None)
        enabled: Whether agent is enabled
        system_prompt: Optional system prompt text
        config: Optional JSON config string (defaults to '{}')

    Returns:
        str: Agent ID
    """
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    if config is None:
        config = "{}"

    with db_conn(db_url) as conn:
        driver_id = _ensure_driver(conn, agent_type)
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, driver_id, config, enabled, system_prompt) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (agent_id, name, agent_type, driver_id, config, enabled, system_prompt),
        )
        conn.commit()

    return agent_id


def create_execution_via_api(
    scheduler_url: str,
    agent_id: str = None,
    prompt: str = "test",
    title: str = None,
    cwd: str = None,
    project_id: str = None,
    branch: str = None,
    context_id: str = None,
    root_agent_id: str = None,
    agent_ids: list = None,
    parts: list = None,
    sandbox_policy: dict = None,
) -> tuple:
    """POST /api/v1/executions, return (execution_id, session_id).

    Args:
        scheduler_url: Base URL of the scheduler
        agent_id: Agent ID (convenience — converted to root_agent_id + agent_ids)
        prompt: User prompt text
        title: Optional execution title
        cwd: Working directory (defaults to tempfile.gettempdir())
        project_id: Optional project ID
        branch: Optional git branch name
        context_id: Optional context ID
        root_agent_id: Root agent ID (new API)
        agent_ids: List of agent IDs for execution pool (new API)

    Returns:
        tuple: (execution_id, session_id)
    """
    # Backward compat: convert old agent_id to new API shape
    if agent_id is not None and root_agent_id is None:
        root_agent_id = agent_id
        agent_ids = agent_ids or [agent_id]

    if cwd is None and project_id is None:
        cwd = tempfile.gettempdir()

    payload_parts = parts if parts is not None else [{"text": prompt}]
    payload = {
        "root_agent_id": root_agent_id,
        "agent_ids": agent_ids,
        "parts": payload_parts,
    }
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
    if sandbox_policy is not None:
        payload["sandbox_policy"] = sandbox_policy

    resp = httpx.post(f"{scheduler_url}/api/v1/executions", json=payload, timeout=5)
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
        session_id: Session whose credential authenticates the call
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
            "Authorization": f"Bearer {agent_token(scheduler_url, session_id)}",
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
        session_id: Session whose credential authenticates the call
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
            "Authorization": f"Bearer {agent_token(scheduler_url, session_id)}",
            "MCP-Protocol-Version": "2025-11-25",
            "Accept": "application/json, text/event-stream",
        },
        timeout=5,
    )


def mcp_tools_list(scheduler_url: str, session_id: str) -> list:
    """Call tools/list, return list of tool name strings.

    Args:
        scheduler_url: Base URL of the scheduler
        session_id: Session whose credential authenticates the call

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
        session_id: Session whose credential authenticates the call
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


def seed_project(
    db_url: str,
    name: str = "test-project",
    path: str = None,
    project_id: str = None,
    agent_ids: list = None,
) -> str:
    """Insert a test project directly into the database.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        name: Project name
        path: Project path (defaults to a temp directory)
        project_id: Project ID (generated UUID if None)
        agent_ids: Optional list of agent IDs to seed into project_agents

    Returns:
        str: Project ID
    """
    if project_id is None:
        project_id = str(uuid.uuid4())
    if path is None:
        path = tempfile.gettempdir()

    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, slug, path, settings) VALUES (?, ?, ?, ?, '{}')",
            (project_id, name, project_id, path),
        )
        if agent_ids:
            for aid in agent_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                    (project_id, aid),
                )
        conn.commit()

    return project_id


def create_project_via_api(scheduler_url: str, name: str, path: str = None) -> dict:
    """POST /api/v1/projects, return response data.

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
        f"{scheduler_url}/api/v1/projects",
        json={"name": name, "path": path},
        timeout=5,
    )
    assert resp.status_code == 201, (
        f"create project failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def ensure_driver_via_api(scheduler_url: str, platform: str = "acp") -> str:
    """Find a driver for the given platform, return driver_id. Raises if not found."""
    resp = httpx.get(f"{scheduler_url}/api/v1/drivers", timeout=5)
    assert resp.status_code == 200
    for driver in resp.json():
        if driver["platform"] == platform:
            return driver["id"]

    raise RuntimeError(
        f"Driver not found for platform '{platform}'. "
        "Drivers are created by migration 0005."
    )


def create_agent_via_api(
    scheduler_url: str,
    name: str,
    driver_id: str = None,
    config: dict = None,
    description: str = None,
) -> dict:
    """POST /api/v1/agents, return response data.

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
        f"{scheduler_url}/api/v1/agents",
        json=payload,
        timeout=5,
    )
    assert resp.status_code == 201, (
        f"create agent failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Helper functions for common test operations
# ---------------------------------------------------------------------------


def worker_sync(url: str, payload: dict = None, timeout: int = 10) -> dict:
    """POST /api/worker/sync with optional JSON body.

    Args:
        url: Base scheduler URL (e.g. "http://localhost:19456")
        payload: Optional JSON body
        timeout: Request timeout in seconds

    Returns:
        dict: Parsed JSON response
    """
    resp = httpx.post(f"{url}/api/worker/sync", json=payload or {}, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def assert_event_exists(
    db_url: str,
    session_id: str,
    event_type: str,
    payload_fragment: str = None,
) -> None:
    """Assert that a matching event exists in the events table.

    Args:
        db_url: Database URL (sqlite:... or postgres://...)
        session_id: Session ID to check events for
        event_type: Expected event_type value
        payload_fragment: If provided, assert this substring appears in at
            least one matching event's payload
    """
    with db_conn(db_url) as conn:
        cur = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = ?",
            (session_id, event_type),
        )
        rows = cur.fetchall()
    assert len(rows) > 0, (
        f"session {session_id}: no events with event_type={event_type!r}"
    )
    if payload_fragment is not None:
        found = any(payload_fragment in row[0] for row in rows)
        assert found, (
            f"session {session_id}: no {event_type!r} event contains {payload_fragment!r}"
        )


# ---------------------------------------------------------------------------
# HTTP endpoint helpers
# ---------------------------------------------------------------------------


def stop_session(url: str, session_id: str) -> httpx.Response:
    """POST /api/v1/sessions/{id}/stop — request session stop.

    Args:
        url: Base scheduler URL
        session_id: Session ID to stop

    Returns:
        httpx.Response: Raw HTTP response
    """
    return httpx.post(f"{url}/api/v1/sessions/{session_id}/stop", timeout=5)


def post_user_message(url: str, session_id: str, text: str) -> httpx.Response:
    """POST /api/v1/sessions/{id}/message with a text payload.

    Args:
        url: Base scheduler URL
        session_id: Session ID to send message to
        text: Message text

    Returns:
        httpx.Response: Raw HTTP response
    """
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={"parts": [{"text": text}]},
        timeout=5,
    )
