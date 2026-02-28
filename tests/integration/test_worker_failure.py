"""Worker failure handling tests using mock scheduler (session protocol).

Verifies the worker handles various failure scenarios gracefully:
1. Agent process failures (nonexistent command)
2. Malformed task data (missing required fields)
3. Agent config validation errors (empty command)
4. Scheduler connection loss
5. Unknown agent types

Run with: uv run pytest tests/integration/test_worker_failure.py -v
"""

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

ACP_MOCK_CONFIG = {
    "command": "uv",
    "args": ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", "acp"],
    "timeout": 30,
}


def _start_worker(scheduler_url):
    return start_worker_with_retry_config(
        scheduler_url=scheduler_url,
        startup_attempts=10,
        reconnect_attempts=10,
        retry_delay_ms=100,
        interval="500ms",
        base_dir=BASE_DIR,
    )


def _enqueue_session(scheduler_url, session_id, execution_id, task_payload):
    """Enqueue a session assignment with custom task payload."""
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_session",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": task_payload,
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue failed: {resp.text}"


def _get_results(scheduler_url):
    return requests.get(f"{scheduler_url}/test/results", timeout=5).json()


def _poll_until(predicate, timeout=30, interval=0.3):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_worker_handles_agent_process_failure():
    """Worker reports error and stays alive when agent command doesn't exist."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(
            url,
            "sess-fail-1",
            "exec-fail-1",
            {
                "agent_id": "bad-agent",
                "driver": {"platform": "acp", "config": {}},
                "agent_config": {
                    "command": "/nonexistent/path/to/agent",
                    "args": [],
                    "timeout": 5,
                },
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        # Worker should report a result (error) back to scheduler
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=15), (
            "Worker did not report session result after agent failure"
        )

        # Worker should still be alive after the failure
        time.sleep(1)
        assert worker.poll() is None, "Worker should survive agent process failure"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_malformed_task_data():
    """Worker handles task payload with no message field gracefully."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        # Payload missing the "message" field entirely — ACP agent starts fine
        # but send_prompt fails because it can't find message.parts or a string
        _enqueue_session(
            url,
            "sess-malformed-1",
            "exec-malformed-1",
            {
                "agent_id": "mock-agent",
                "driver": {"platform": "acp", "config": {}},
                "agent_config": ACP_MOCK_CONFIG,
                # No "message" field
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        # Worker should report a result even for malformed payloads
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report result for malformed task"
        )

        # Worker should still be alive
        time.sleep(1)
        assert worker.poll() is None, "Worker should survive malformed task data"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_surfaces_adapter_rejection():
    """Worker surfaces agent config validation errors in session result."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        # Empty command triggers AcpConfig::validate() rejection
        _enqueue_session(
            url,
            "sess-reject-1",
            "exec-reject-1",
            {
                "agent_id": "bad-config-agent",
                "driver": {"platform": "acp", "config": {}},
                "agent_config": {
                    "command": "",
                    "args": [],
                    "timeout": 5,
                },
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        # Worker should sync a result back despite the validation error
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=15), (
            "Worker did not report result for rejected agent config"
        )

        # Worker should stay alive after validation error
        time.sleep(1)
        assert worker.poll() is None, (
            "Worker should survive agent config validation failure"
        )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_orchestrator_connection_loss():
    """Worker exits after scheduler becomes permanently unreachable."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        # Fast retry config: 5 reconnect attempts × 100ms = 0.5s tolerance
        worker = start_worker_with_retry_config(
            scheduler_url=url,
            startup_attempts=3,
            reconnect_attempts=5,
            retry_delay_ms=100,
            interval="500ms",
            base_dir=BASE_DIR,
        )
        processes.append(worker)

        # Let worker establish connection
        time.sleep(1.5)
        assert worker.poll() is None, "Worker should be running initially"

        # Kill the scheduler to simulate permanent connection loss
        scheduler_proc.terminate()
        scheduler_proc.wait(timeout=5)
        processes.remove(scheduler_proc)

        # Worker should exit after exhausting reconnect attempts
        exit_code = worker.wait(timeout=5)
        assert exit_code == 1, (
            f"Worker should exit with code 1 after connection loss, got {exit_code}"
        )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_fails_on_unknown_agent():
    """Worker reports error for unsupported driver platform."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(
            url,
            "sess-unknown-1",
            "exec-unknown-1",
            {
                "agent_id": "unknown-agent",
                "driver": {"platform": "nonexistent_protocol", "config": {}},
                "agent_config": {"command": "echo", "args": ["hi"]},
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        # Worker should report back an error result
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=15), (
            "Worker did not report result for unknown agent type"
        )

        # Worker should still be alive
        time.sleep(1)
        assert worker.poll() is None, "Worker should survive unknown agent type error"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)
