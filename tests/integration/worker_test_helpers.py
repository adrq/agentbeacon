"""Shared test helpers for worker integration tests.

Extracted from test_worker_session_protocol.py to avoid fragile
cross-test-file imports.
"""

import time
from pathlib import Path

import requests

from tests.testhelpers import (
    PortManager,
    start_mock_scheduler,
    start_worker_with_retry_config,
    wait_for_port,
)

BASE_DIR = Path(__file__).parent.parent.parent


def create_mock_scheduler():
    """Start mock scheduler and return (url, port, process)."""
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    proc = start_mock_scheduler(port, base_dir=BASE_DIR)
    assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
    return f"http://127.0.0.1:{port}", port, proc, pm


def start_worker(scheduler_url):
    """Start worker with fast retry config for tests."""
    return start_worker_with_retry_config(
        scheduler_url=scheduler_url,
        startup_attempts=10,
        reconnect_attempts=10,
        retry_delay_ms=100,
        interval="500ms",
        base_dir=BASE_DIR,
    )


def clear_state(scheduler_url):
    """Clear mock scheduler state."""
    requests.post(f"{scheduler_url}/test/clear", timeout=5)


def enqueue_session(
    scheduler_url,
    session_id="sess-1",
    execution_id="exec-1",
    prompt_text="hello from test",
):
    """Enqueue a session with ACP mock agent config."""
    task_payload = {
        "agent_id": "mock-agent",
        "agent_type": "acp",
        "agent_config": {
            "command": "uv",
            "args": ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", "acp"],
            "timeout": 30,
        },
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
    assert resp.status_code == 200, f"Enqueue session failed: {resp.text}"


def enqueue_prompt(
    scheduler_url,
    session_id="sess-1",
    execution_id="exec-1",
    prompt_text="follow-up prompt",
):
    """Enqueue a follow-up prompt delivery."""
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": {
                "message": {"parts": [{"kind": "text", "text": prompt_text}]}
            },
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue prompt failed: {resp.text}"


def mark_complete(scheduler_url, session_id="sess-1"):
    """Mark a session as complete."""
    resp = requests.post(
        f"{scheduler_url}/test/mark_complete",
        json={"sessionId": session_id},
        timeout=5,
    )
    assert resp.status_code == 200, f"Mark complete failed: {resp.text}"


def send_command(scheduler_url, command):
    """Queue a command (cancel/shutdown)."""
    resp = requests.post(
        f"{scheduler_url}/test/send_command",
        json={"command": command},
        timeout=5,
    )
    assert resp.status_code == 200, f"Send command failed: {resp.text}"


def get_sync_log(scheduler_url):
    """Get all sync requests received by mock scheduler."""
    resp = requests.get(f"{scheduler_url}/test/sync_log", timeout=5)
    return resp.json()


def get_results(scheduler_url):
    """Get all session results reported by worker."""
    resp = requests.get(f"{scheduler_url}/test/results", timeout=5)
    return resp.json()


def get_events(scheduler_url):
    """Get all mid-turn message events posted by worker."""
    resp = requests.get(f"{scheduler_url}/test/events", timeout=5)
    return resp.json()


def get_agent_output(scheduler_url, session_id="sess-1"):
    """Get agent output from mid-turn events, falling back to sync results.

    Mid-turn messages are posted in real-time and also included in sync results
    (dedup via msg_seq). This helper reads events first (most common path),
    falling back to turnMessages in sync results.
    """
    events = get_events(scheduler_url)
    session_events = [e for e in events if e.get("sessionId") == session_id]

    all_parts = []
    for evt in session_events:
        payload = evt.get("payload", {})
        if (
            isinstance(payload, dict)
            and payload.get("role") == "agent"
            and "parts" in payload
        ):
            all_parts.extend(payload["parts"])

    if all_parts:
        return {"role": "agent", "parts": all_parts}

    # Fallback: aggregate sync results via turn_messages
    results = get_results(scheduler_url)
    fallback_parts = []
    for r in results:
        if r.get("sessionId") != session_id:
            continue
        for msg in r.get("turnMessages") or []:
            payload = msg.get("payload", {})
            if (
                isinstance(payload, dict)
                and payload.get("role") == "agent"
                and "parts" in payload
            ):
                fallback_parts.extend(payload["parts"])

    if fallback_parts:
        return {"role": "agent", "parts": fallback_parts}

    return None


def poll_until(predicate, timeout=15, interval=0.3):
    """Poll until predicate returns True or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False
