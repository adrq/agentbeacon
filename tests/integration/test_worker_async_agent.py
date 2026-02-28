"""Worker session lifecycle tests using mock scheduler (session protocol).

Tests the worker's handling of session lifecycle events that go beyond
a simple single-prompt cycle:
1. Cancel command while worker is waiting for events
2. Cancel command delivered alongside prompt result
3. Multi-turn sessions with prompt delivery
4. Session completion with output verification

Run with: uv run pytest tests/integration/test_worker_async_agent.py -v
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
from tests.integration.worker_test_helpers import get_agent_output

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
    """Enqueue a session assignment with ACP mock agent."""
    task_payload = {
        "agent_id": "mock-agent",
        "driver": {"platform": "acp", "config": {}},
        "agent_config": ACP_MOCK_CONFIG,
        "message": {"role": "user", "parts": [{"kind": "text", "text": prompt_text}]},
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
    """Enqueue a follow-up prompt delivery for an active session."""
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": prompt_text}],
                },
            },
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue prompt failed: {resp.text}"


def _send_command(scheduler_url, command):
    """Queue a command (cancel/shutdown)."""
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
    """Worker processes prompt to completion and reports output in result.

    Verifies the end-to-end path: prompt → ACP agent → output captured → result reported.
    """
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

        # Output is delivered via mid-turn events (or sync result as fallback)
        output = get_agent_output(url, "sess-async-1")
        assert output is not None, (
            f"Expected agent output from events or sync: {result}"
        )
        assert output["role"] == "agent"
        assert len(output["parts"]) > 0
    finally:
        _mark_complete(f"http://localhost:{port}", "sess-async-1")
        time.sleep(0.5)
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_cancel_while_waiting():
    """Worker exits session cleanly when cancel arrives during waiting_for_event.

    After reporting a result, worker enters waiting_for_event. A cancel command
    should cause the worker to leave the session and return to idle.
    """
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

        # Wait for first result (worker will enter waiting_for_event after)
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report initial result"
        )

        # Send cancel command — worker should receive it in waiting_for_event
        _send_command(url, "cancel")

        # Give worker time to process cancel and return to idle
        time.sleep(3)

        # Worker should still be alive (returned to idle after cancel)
        assert worker.poll() is None, "Worker should survive cancel and return to idle"

        # Verify sync log shows waiting_for_event state before cancel
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
    """Worker handles cancel delivered in the sync response to a result report.

    Scheduler can respond to a sessionResult sync with a cancel command directly,
    which the worker should handle without entering waiting_for_event.
    """
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        # Queue cancel BEFORE session so it's ready when worker reports result
        _send_command(url, "cancel")
        _enqueue_session(
            url, "sess-cancel-2", "exec-cancel-2", "prompt then immediate cancel"
        )

        worker = _start_worker(url)
        processes.append(worker)

        # Wait for result
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        # Give worker time to process the cancel response
        time.sleep(3)

        # Worker should still be alive (returned to idle after cancel)
        assert worker.poll() is None, "Worker should survive cancel-after-result"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_multi_turn_session():
    """Worker processes two turns in the same session via prompt delivery.

    First turn: initial session assignment with prompt.
    Second turn: follow-up prompt delivered while waiting_for_event.
    Both turns should produce results with the same sessionId.
    """
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(url, "sess-multi-1", "exec-multi-1", "first turn")
        # Queue follow-up prompt before worker starts so it's ready in waiting_for_event
        _enqueue_prompt(url, "sess-multi-1", "exec-multi-1", "second turn")

        worker = _start_worker(url)
        processes.append(worker)

        # Wait for two results (one per turn)
        assert _poll_until(lambda: len(_get_results(url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(_get_results(url))}: {_get_results(url)}"
        )

        results = _get_results(url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-multi-1"
        assert results[1]["sessionId"] == "sess-multi-1"

        # Both results should have the same agentSessionId (same ACP subprocess)
        assert results[0]["agentSessionId"] == results[1]["agentSessionId"], (
            f"Both turns should use same agent session: "
            f"{results[0]['agentSessionId']} vs {results[1]['agentSessionId']}"
        )

        # Both turns should produce output (via mid-turn events or sync)
        output = get_agent_output(url, "sess-multi-1")
        assert output is not None, (
            "Expected agent output from events or sync for multi-turn session"
        )
        assert len(output["parts"]) > 0
        # Verify both turns contributed output (mock agent echoes prompt text)
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
