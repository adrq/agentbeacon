# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
from pathlib import Path

import requests

from tests.testhelpers import (
    PortManager,
    start_worker_with_retry_config,
    wait_for_port,
)
from tests.mock_agent_helpers import start_mock_scheduler

BASE_DIR = Path(__file__).parent.parent


def create_mock_scheduler():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    proc = start_mock_scheduler(port, base_dir=BASE_DIR)
    assert wait_for_port(port, timeout=10), "Mock scheduler did not start"
    return f"http://127.0.0.1:{port}", port, proc, pm


def start_worker(scheduler_url, extra_env=None, inactivity_timeout=None):
    extra_args = None
    if inactivity_timeout is not None:
        extra_args = ["--inactivity-timeout", inactivity_timeout]
    return start_worker_with_retry_config(
        scheduler_url=scheduler_url,
        startup_attempts=10,
        reconnect_attempts=10,
        retry_delay_ms=100,
        interval="500ms",
        base_dir=BASE_DIR,
        extra_env=extra_env,
        extra_args=extra_args,
    )


def clear_state(scheduler_url):
    requests.post(f"{scheduler_url}/test/clear", timeout=5)


def enqueue_session(
    scheduler_url,
    session_id="sess-1",
    execution_id="exec-1",
    prompt_text="hello from test",
    extra_args=None,
    parent_name=None,
    platform="acp",
):
    if platform == "claude_sdk":
        task_payload = {
            "agent_id": "mock-agent",
            "driver": {
                "platform": "claude_sdk",
                "config": {"fs_level": "unrestricted"},
            },
            "agent_config": {},
            "message": {"role": "ROLE_USER", "parts": [{"text": prompt_text}]},
        }
    else:
        mode = "codex" if platform == "codex" else "acp"
        args = ["run", "python", "-m", "agentbeacon.mock_agent", "--mode", mode]
        if extra_args:
            args.extend(extra_args)

        driver_platform = "codex_sdk" if platform == "codex" else "acp"
        task_payload = {
            "agent_id": "mock-agent",
            "driver": {"platform": driver_platform, "config": {}},
            "agent_config": {
                "command": "uv",
                "args": args,
                "timeout": 30,
            },
            "message": {"role": "ROLE_USER", "parts": [{"text": prompt_text}]},
        }

    body = {
        "sessionId": session_id,
        "executionId": execution_id,
        "taskPayload": task_payload,
    }
    if parent_name is not None:
        body["parent_name"] = parent_name
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_session",
        json=body,
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue session failed: {resp.text}"


def enqueue_prompt(
    scheduler_url,
    session_id="sess-1",
    execution_id="exec-1",
    prompt_text="follow-up prompt",
):
    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": session_id,
            "executionId": execution_id,
            "taskPayload": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": prompt_text}],
                }
            },
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue prompt failed: {resp.text}"


def get_sync_log(scheduler_url):
    resp = requests.get(f"{scheduler_url}/test/sync_log", timeout=5)
    return resp.json()


def get_results(scheduler_url):
    resp = requests.get(f"{scheduler_url}/test/results", timeout=5)
    return resp.json()


def get_events(scheduler_url):
    resp = requests.get(f"{scheduler_url}/test/events", timeout=5)
    return resp.json()


def get_agent_output(scheduler_url, session_id="sess-1"):
    events = get_events(scheduler_url)
    session_events = [e for e in events if e.get("sessionId") == session_id]

    all_parts = []
    for evt in session_events:
        payload = evt.get("payload", {})
        if (
            isinstance(payload, dict)
            and payload.get("role") == "ROLE_AGENT"
            and "parts" in payload
        ):
            all_parts.extend(payload["parts"])

    if all_parts:
        return {"role": "ROLE_AGENT", "parts": all_parts}

    results = get_results(scheduler_url)
    fallback_parts = []
    for r in results:
        if r.get("sessionId") != session_id:
            continue
        for msg in r.get("turnMessages") or []:
            payload = msg.get("payload", {})
            if (
                isinstance(payload, dict)
                and payload.get("role") == "ROLE_AGENT"
                and "parts" in payload
            ):
                fallback_parts.extend(payload["parts"])

    if fallback_parts:
        return {"role": "ROLE_AGENT", "parts": fallback_parts}

    return None


def poll_until(predicate, timeout=15, interval=0.3):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def mark_complete(scheduler_url, session_id="sess-1"):
    resp = requests.post(
        f"{scheduler_url}/test/mark_complete",
        json={"sessionId": session_id},
        timeout=5,
    )
    assert resp.status_code == 200, f"Mark complete failed: {resp.text}"


def get_raw_sync_log(scheduler_url):
    resp = requests.get(f"{scheduler_url}/test/raw_sync_log", timeout=5)
    return resp.json()


def send_command(scheduler_url, command):
    resp = requests.post(
        f"{scheduler_url}/test/send_command",
        json={"command": command},
        timeout=5,
    )
    assert resp.status_code == 200, f"Send command failed: {resp.text}"
