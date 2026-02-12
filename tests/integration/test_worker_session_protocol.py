"""Worker session-based sync protocol tests using mock scheduler.

Tests the worker's behavior against a simple mock scheduler that speaks
the session-based sync protocol. These are worker-in-isolation tests:
the mock scheduler has no real DB, just in-memory queues.
"""

import time
from pathlib import Path

import pytest
import requests

from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_mock_scheduler,
    start_worker_with_retry_config,
    wait_for_port,
)

BASE_DIR = Path(__file__).parent.parent.parent


@pytest.fixture()
def mock_scheduler():
    """Start mock scheduler and yield (url, port, process)."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    proc = start_mock_scheduler(port, base_dir=BASE_DIR)
    assert wait_for_port(port, timeout=10), "Mock scheduler did not start"

    yield f"http://127.0.0.1:{port}", port, proc

    cleanup_processes([proc])
    pm.release_port(port)


def _start_worker(scheduler_url):
    """Start worker with fast retry config for tests."""
    return start_worker_with_retry_config(
        scheduler_url=scheduler_url,
        startup_attempts=10,
        reconnect_attempts=10,
        retry_delay_ms=100,
        interval="500ms",
        base_dir=BASE_DIR,
    )


def _clear_state(scheduler_url):
    """Clear mock scheduler state."""
    requests.post(f"{scheduler_url}/test/clear", timeout=5)


def _enqueue_session(scheduler_url, session_id="sess-1", execution_id="exec-1"):
    """Enqueue a session assignment with ACP mock agent config."""
    task_payload = {
        "agent_id": "mock-agent",
        "agent_type": "acp",
        "agent_config": {
            "command": "uv",
            "args": ["run", "python", "-m", "agentmaestro.mock_agent", "--mode", "acp"],
            "timeout": 30,
        },
        "sandbox_config": {},
        "message": {
            "parts": [{"kind": "text", "text": "hello from test"}],
        },
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
    assert resp.status_code == 200, f"Enqueue session failed: {resp.text}"


def _enqueue_prompt(scheduler_url, session_id="sess-1", execution_id="exec-1"):
    """Enqueue a follow-up prompt delivery."""
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": {
                "message": {"parts": [{"kind": "text", "text": "follow-up prompt"}]}
            },
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue prompt failed: {resp.text}"


def _mark_complete(scheduler_url, session_id="sess-1"):
    """Mark a session as complete."""
    resp = requests.post(
        f"{scheduler_url}/test/mark_complete",
        json={"sessionId": session_id},
        timeout=5,
    )
    assert resp.status_code == 200, f"Mark complete failed: {resp.text}"


def _send_command(scheduler_url, command):
    """Queue a command (cancel/shutdown)."""
    resp = requests.post(
        f"{scheduler_url}/test/send_command",
        json={"command": command},
        timeout=5,
    )
    assert resp.status_code == 200, f"Send command failed: {resp.text}"


def _get_sync_log(scheduler_url):
    """Get all sync requests received by mock scheduler."""
    resp = requests.get(f"{scheduler_url}/test/sync_log", timeout=5)
    return resp.json()


def _get_results(scheduler_url):
    """Get all session results reported by worker."""
    resp = requests.get(f"{scheduler_url}/test/results", timeout=5)
    return resp.json()


def _poll_until(predicate, timeout=15, interval=0.3):
    """Poll until predicate returns True or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_worker_idle_gets_no_action(mock_scheduler):
    """Worker with empty queue stays alive and sends idle syncs."""
    scheduler_url, _, scheduler_proc = mock_scheduler
    _clear_state(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        time.sleep(3)

        # Worker should still be alive
        assert worker.poll() is None, "Worker should still be running"

        # Should have sent multiple idle syncs (empty {})
        sync_log = _get_sync_log(scheduler_url)
        assert len(sync_log) >= 2, f"Expected multiple idle syncs, got {len(sync_log)}"

        # Idle syncs should be empty objects (skip_serializing_if omits None fields)
        for entry in sync_log:
            assert entry == {}, f"Idle sync should be empty: {entry}"
    finally:
        cleanup_processes([worker])


def test_worker_receives_session_assignment(mock_scheduler):
    """Worker receives session assignment and reports result."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        # Wait for worker to report a result
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 1
        assert results[0]["sessionId"] == "sess-1"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_reports_agent_session_id(mock_scheduler):
    """Worker reports non-null agentSessionId from ACP agent."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(scheduler_url)
        assert results[0]["agentSessionId"] is not None, (
            f"Expected non-null agentSessionId, got {results[0]}"
        )
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_enters_waiting_after_result(mock_scheduler):
    """After reporting result, worker enters waiting_for_event."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        # Wait for result
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30)

        # Wait a bit for worker to enter waiting_for_event
        time.sleep(2)

        sync_log = _get_sync_log(scheduler_url)
        waiting_syncs = [
            entry
            for entry in sync_log
            if entry.get("sessionState", {}).get("status") == "waiting_for_event"
        ]
        assert len(waiting_syncs) > 0, (
            f"Expected waiting_for_event syncs, got: {sync_log}"
        )
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_receives_prompt_delivery(mock_scheduler):
    """Worker processes initial session + follow-up prompt (two results)."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)
    # Queue follow-up prompt BEFORE worker starts so it's ready when worker enters waiting
    _enqueue_prompt(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        # Wait for two results (initial prompt + follow-up)
        assert _poll_until(lambda: len(_get_results(scheduler_url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(_get_results(scheduler_url))}: {_get_results(scheduler_url)}"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-1"
        assert results[1]["sessionId"] == "sess-1"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_receives_string_prompt_delivery(mock_scheduler):
    """Worker processes a string taskPayload (user reply format)."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    # Queue a string prompt (user answer format) instead of object
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": "sess-1",
            "executionId": "exec-1",
            "taskPayload": "[user]\n\nfollow-up as plain string",
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue string prompt failed: {resp.text}"

    worker = _start_worker(scheduler_url)
    try:
        # Wait for two results (initial object prompt + string follow-up)
        assert _poll_until(lambda: len(_get_results(scheduler_url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(_get_results(scheduler_url))}: {_get_results(scheduler_url)}"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-1"
        assert results[1]["sessionId"] == "sess-1"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_handles_session_complete(mock_scheduler):
    """After session_complete, worker returns to idle."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)
    _mark_complete(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        # Wait for result then session complete
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30)

        # Give worker time to process session_complete and return to idle
        time.sleep(3)

        # Worker should still be alive (returned to idle)
        assert worker.poll() is None, (
            "Worker should still be running after session_complete"
        )

        # Check sync log for idle requests after the session
        sync_log = _get_sync_log(scheduler_url)
        # Find idle syncs (empty objects) that appear after result syncs
        has_result = False
        idle_after_result = False
        for entry in sync_log:
            if "sessionResult" in entry and entry["sessionResult"]:
                has_result = True
            elif has_result and entry == {}:
                idle_after_result = True
                break

        assert idle_after_result, (
            f"Expected idle sync after session result, sync_log: {sync_log}"
        )
    finally:
        cleanup_processes([worker])


def test_worker_handles_shutdown_command(mock_scheduler):
    """Worker exits cleanly on shutdown command."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _send_command(scheduler_url, "shutdown")

    worker = _start_worker(scheduler_url)
    try:
        # Worker should exit within a few seconds
        assert _poll_until(lambda: worker.poll() is not None, timeout=10), (
            "Worker did not exit after shutdown command"
        )
    finally:
        cleanup_processes([worker])


def test_worker_invalid_agent_graceful(mock_scheduler):
    """Worker doesn't crash on bad agent config (nonexistent command)."""
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    # Enqueue session with broken agent config
    bad_payload = {
        "agent_id": "bad-agent",
        "agent_type": "acp",
        "agent_config": {
            "command": "/nonexistent/path/to/agent",
            "args": [],
            "timeout": 5,
        },
        "sandbox_config": {},
        "message": {
            "parts": [{"kind": "text", "text": "hello"}],
        },
    }
    requests.post(
        f"{scheduler_url}/test/enqueue_session",
        json={
            "sessionId": "sess-bad",
            "executionId": "exec-bad",
            "taskPayload": bad_payload,
        },
        timeout=5,
    )

    worker = _start_worker(scheduler_url)
    try:
        # Wait a few seconds for worker to attempt and fail
        time.sleep(5)

        # Worker should still be alive
        assert worker.poll() is None, "Worker should not crash on bad agent config"
    finally:
        cleanup_processes([worker])
