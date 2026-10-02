# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
from pathlib import Path

import requests

from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_worker_with_retry_config,
    wait_for_port,
)
from tests.mock_agent_helpers import start_mock_scheduler

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
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=15), (
            "Worker did not report session result after agent failure"
        )

        time.sleep(1)
        assert worker.poll() is None, "Worker should survive agent process failure"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_malformed_task_data():
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
            "sess-malformed-1",
            "exec-malformed-1",
            {
                "agent_id": "mock-agent",
                "driver": {"platform": "acp", "config": {}},
                "agent_config": ACP_MOCK_CONFIG,
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report result for malformed task"
        )

        time.sleep(1)
        assert worker.poll() is None, "Worker should survive malformed task data"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_surfaces_adapter_rejection():
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
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=15), (
            "Worker did not report result for rejected agent config"
        )

        time.sleep(1)
        assert worker.poll() is None, (
            "Worker should survive agent config validation failure"
        )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_orchestrator_connection_loss():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        worker = start_worker_with_retry_config(
            scheduler_url=url,
            startup_attempts=3,
            reconnect_attempts=5,
            retry_delay_ms=100,
            interval="500ms",
            base_dir=BASE_DIR,
        )
        processes.append(worker)

        time.sleep(1.5)
        assert worker.poll() is None, "Worker should be running initially"

        scheduler_proc.terminate()
        scheduler_proc.wait(timeout=5)
        processes.remove(scheduler_proc)

        exit_code = worker.wait(timeout=5)
        assert exit_code == 1, (
            f"Worker should exit with code 1 after connection loss, got {exit_code}"
        )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_fails_on_unknown_agent():
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
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
            },
        )

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=15), (
            "Worker did not report result for unknown agent type"
        )

        time.sleep(1)
        assert worker.poll() is None, "Worker should survive unknown agent type error"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)
