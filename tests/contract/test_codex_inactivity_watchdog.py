# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    get_session_row,
    seed_codex_test_agent,
)

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_mock_sdks_dir = os.path.join(_project_root, "tests", "mock_sdks")
_EXECUTOR_ENV = {
    "AGENTBEACON_EXECUTORS_DIR": os.path.join(_mock_sdks_dir, "executors"),
    "NODE_OPTIONS": "--preserve-symlinks --preserve-symlinks-main",
}


def _poll_until(predicate, timeout=30, interval=0.5):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _session_executor_state(db_url, session_id):
    row = get_session_row(db_url, session_id)
    return row.get("executor_state")


def _get_all_events(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [(et, json.loads(p)) for et, p in rows]


def _codex_agent_message_count(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY id",
            (session_id,),
        ).fetchall()
    count = 0
    for (p,) in rows:
        payload = json.loads(p)
        for part in payload.get("parts", []):
            data = part.get("data")
            if not isinstance(data, dict):
                continue
            if data.get("method") == "item/completed":
                item = data.get("params", {}).get("item", {})
                if item.get("type") == "agentMessage":
                    count += 1
            elif data.get("type") == "agentMessage":
                count += 1
    return count


def _has_stalled_event(db_url, session_id):
    events = _get_all_events(db_url, session_id)
    for _et, payload in events:
        text = json.dumps(payload)
        if "executor stalled" in text or "no output for" in text:
            return True
    return False


def _start_worker_with_inactivity_timeout(
    scheduler_url, inactivity_timeout="2s", interval="500ms"
):
    base_dir = Path(__file__).parent.parent.parent
    worker_env = os.environ.copy()
    worker_env.update(_EXECUTOR_ENV)
    cmd = [
        "./bin/agentbeacon-worker",
        "--scheduler-url",
        scheduler_url,
        "--interval",
        interval,
        "--inactivity-timeout",
        inactivity_timeout,
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=base_dir,
        env=worker_env,
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_idle_follow_up_no_crash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "first turn for idle crash test"
        )

        worker = _start_worker_with_inactivity_timeout(ctx["url"], "2s")
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after first turn"

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) == 1,
                timeout=10,
            ), "Expected exactly 1 item/completed event after first turn"

            time.sleep(4)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "follow-up after idle"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) == 2,
                timeout=30,
            ), "Expected exactly 2 item/completed events after second turn"

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after follow-up"

            assert not _has_stalled_event(ctx["db_url"], session_id), (
                "Follow-up after idle should NOT trigger 'executor stalled' watchdog"
            )

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_inactivity_watchdog_fires(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "HANG")

        worker = _start_worker_with_inactivity_timeout(ctx["url"], "2s")
        try:
            assert _poll_until(
                lambda: _has_stalled_event(ctx["db_url"], session_id),
                timeout=30,
            ), (
                f"HANG session should trigger 'executor stalled' event. "
                f"State: {get_session_row(ctx['db_url'], session_id)}"
            )

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "HANG session should recover to idle after stall"

            assert worker.poll() is None, "Worker should not crash on session stall"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_multiple_idle_periods_no_staleness(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "turn one")

        worker = _start_worker_with_inactivity_timeout(ctx["url"], "2s")
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Turn 1 did not reach idle"
            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) == 1,
                timeout=10,
            ), "Expected exactly 1 item/completed after turn 1"

            time.sleep(3)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "turn two"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"turn 2 push failed: {resp.text}"

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) == 2,
                timeout=30,
            ), "Expected exactly 2 item/completed after turn 2"
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Turn 2 did not reach idle"

            time.sleep(3)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "turn three"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"turn 3 push failed: {resp.text}"

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) == 3,
                timeout=30,
            ), "Expected exactly 3 item/completed after turn 3"
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Turn 3 did not reach idle"

            assert not _has_stalled_event(ctx["db_url"], session_id), (
                "Multiple idle periods should not trigger 'executor stalled'"
            )

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )
        finally:
            cleanup_processes([worker])
