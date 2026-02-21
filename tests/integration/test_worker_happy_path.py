"""Worker happy-path tests using mock scheduler (session protocol).

Verifies the complete worker lifecycle:
1. Worker polls and receives a session assignment
2. Worker starts ACP executor and processes prompt
3. Worker reports session result (with agent output) back to scheduler
4. Worker returns to idle and picks up next session

Run with: uv run pytest tests/integration/test_worker_happy_path.py -v
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
    "args": ["run", "python", "-m", "agentmaestro.mock_agent", "--mode", "acp"],
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
        "agent_type": "acp",
        "agent_config": ACP_MOCK_CONFIG,
        "sandbox_config": {},
        "message": {"parts": [{"kind": "text", "text": prompt_text}]},
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


def _get_results(scheduler_url):
    return requests.get(f"{scheduler_url}/test/results", timeout=5).json()


def _get_sync_log(scheduler_url):
    return requests.get(f"{scheduler_url}/test/sync_log", timeout=5).json()


def _mark_complete(scheduler_url, session_id):
    requests.post(
        f"{scheduler_url}/test/mark_complete",
        json={"sessionId": session_id},
        timeout=5,
    )


def _poll_until(predicate, timeout=30, interval=0.3):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_worker_complete_task_execution_cycle():
    """Complete cycle: idle -> session assignment -> execute -> report result -> idle."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(url, "sess-hp-1", "exec-hp-1", "hello from happy path")

        worker = _start_worker(url)
        processes.append(worker)

        # Wait for worker to report a result
        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(url)
        assert results[0]["sessionId"] == "sess-hp-1"
        assert results[0]["agentSessionId"] is not None, (
            "Worker should report non-null agentSessionId"
        )

        # Mark session complete so worker returns to idle
        _mark_complete(url, "sess-hp-1")
        time.sleep(2)

        # Worker should still be alive (returned to idle)
        assert worker.poll() is None, "Worker should be alive after returning to idle"

        # Verify sync log shows idle → session → result → idle pattern
        sync_log = _get_sync_log(url)
        has_result = any(
            e.get("sessionResult")
            and e["sessionResult"].get("sessionId") == "sess-hp-1"
            for e in sync_log
        )
        assert has_result, f"Sync log should contain sessionResult: {sync_log}"
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_task_with_output():
    """Worker captures agent output and includes it in session result."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        _enqueue_session(url, "sess-out-1", "exec-out-1", "generate some output")

        worker = _start_worker(url)
        processes.append(worker)

        assert _poll_until(lambda: len(_get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(url)
        result = results[0]

        # Output is delivered via mid-turn events (or sync result as fallback)
        output = get_agent_output(url, "sess-out-1")
        assert output is not None, (
            f"Expected agent output from events or sync: {result}"
        )
        assert output["role"] == "agent", f"Output role should be 'agent': {output}"
        assert len(output["parts"]) > 0, f"Output should have parts: {output}"

        # The mock ACP agent echoes back the prompt
        parts_text = str(output["parts"])
        assert "generate some output" in parts_text, (
            f"Agent output should contain prompt echo: {parts_text}"
        )
    finally:
        _mark_complete(f"http://localhost:{port}", "sess-out-1")
        time.sleep(0.5)
        cleanup_processes(processes)
        pm.release_port(port)


def test_multiple_task_execution_sequence():
    """Worker processes two sequential sessions via mock scheduler."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
        url = f"http://localhost:{port}"

        # Enqueue first session
        _enqueue_session(url, "sess-seq-1", "exec-seq-1", "first sequential task")

        worker = _start_worker(url)
        processes.append(worker)

        # Wait for first result
        assert _poll_until(lambda: len(_get_results(url)) >= 1, timeout=30), (
            "Worker did not complete first session"
        )
        _mark_complete(url, "sess-seq-1")

        # Give worker time to return to idle, then enqueue second session
        time.sleep(2)
        _enqueue_session(url, "sess-seq-2", "exec-seq-2", "second sequential task")

        # Wait for second result
        assert _poll_until(lambda: len(_get_results(url)) >= 2, timeout=30), (
            f"Worker did not complete second session: {_get_results(url)}"
        )

        results = _get_results(url)
        session_ids = [r["sessionId"] for r in results]
        assert "sess-seq-1" in session_ids, "First session should be in results"
        assert "sess-seq-2" in session_ids, "Second session should be in results"

        # Verify sync log shows multiple sync calls
        sync_log = _get_sync_log(url)
        assert len(sync_log) >= 4, (
            f"Expected at least 4 syncs for 2 sessions: {len(sync_log)}"
        )
    finally:
        _mark_complete(f"http://localhost:{port}", "sess-seq-2")
        time.sleep(0.5)
        cleanup_processes(processes)
        pm.release_port(port)
