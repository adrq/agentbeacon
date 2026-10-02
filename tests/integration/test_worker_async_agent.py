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
from tests.worker_test_helpers import get_agent_output

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


def _enqueue_session(scheduler_url, session_id, execution_id, prompt_text):
    task_payload = {
        "agent_id": "mock-agent",
        "driver": {"platform": "acp", "config": {}},
        "agent_config": ACP_MOCK_CONFIG,
        "message": {"role": "ROLE_USER", "parts": [{"text": prompt_text}]},
    }
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


def _enqueue_prompt(scheduler_url, session_id, execution_id, prompt_text):
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": prompt_text}],
                },
            },
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue prompt failed: {resp.text}"


def _send_command(scheduler_url, command):
    resp = requests.post(
        f"{scheduler_url}/test/send_command",
        json={"command": command},
        timeout=5,
    )
    assert resp.status_code == 200, f"Send command failed: {resp.text}"


def _mark_complete(scheduler_url, session_id):
    requests.post(
        f"{scheduler_url}/test/mark_complete",
        json={"sessionId": session_id},
        timeout=5,
    )


def _get_results(scheduler_url):
    return requests.get(f"{scheduler_url}/test/results", timeout=5).json()


def _get_sync_log(scheduler_url):
    return requests.get(f"{scheduler_url}/test/sync_log", timeout=5).json()


def _poll_until(predicate, timeout=30, interval=0.3):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_worker_completes_session_with_output():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(url, "sess-async-1", "exec-async-1", "test prompt for output")

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(url)
        result = results[0]
        assert result["sessionId"] == "sess-async-1"
        assert result["agentSessionId"] is not None

        output = get_agent_output(url, "sess-async-1")
        assert output is not None, (
            f"Expected agent output from events or sync: {result}"
        )
        assert output["role"] == "ROLE_AGENT"
        assert len(output["parts"]) > 0
    finally:
        _mark_complete(f"http://localhost:{port}", "sess-async-1")
        time.sleep(0.5)
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_cancel_while_waiting():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(url, "sess-cancel-1", "exec-cancel-1", "prompt before cancel")

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report initial result"
        )

        _send_command(url, "cancel")

        time.sleep(3)

        assert worker.poll() is None, "Worker should survive cancel and return to idle"

        sync_log = _get_sync_log(url)
        has_waiting = any(
            e.get("sessionState", {}).get("status") == "waiting_for_event"
            for e in sync_log
        )
        assert has_waiting, f"Worker should have entered waiting_for_event: {sync_log}"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_cancel_after_result():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _send_command(url, "cancel")
        _enqueue_session(
            url, "sess-cancel-2", "exec-cancel-2", "prompt then immediate cancel"
        )

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        time.sleep(3)

        assert worker.poll() is None, "Worker should survive cancel-after-result"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_multi_turn_session():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(url, "sess-multi-1", "exec-multi-1", "first turn")
        _enqueue_prompt(url, "sess-multi-1", "exec-multi-1", "second turn")

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(_get_results(url))}: {_get_results(url)}"
        )

        results = _get_results(url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-multi-1"
        assert results[1]["sessionId"] == "sess-multi-1"

        assert results[0]["agentSessionId"] == results[1]["agentSessionId"], (
            f"Both turns should use same agent session: "
            f"{results[0]['agentSessionId']} vs {results[1]['agentSessionId']}"
        )

        output = get_agent_output(url, "sess-multi-1")
        assert output is not None, (
            "Expected agent output from events or sync for multi-turn session"
        )
        assert len(output["parts"]) > 0
        parts_text = str(output["parts"])
        assert "first turn" in parts_text, (
            f"Turn 1 output missing from events: {parts_text}"
        )
        assert "second turn" in parts_text, (
            f"Turn 2 output missing from events: {parts_text}"
        )
    finally:
        _mark_complete(f"http://localhost:{port}", "sess-multi-1")
        time.sleep(0.5)
        cleanup_processes(processes)
        pm.release_port(port)
