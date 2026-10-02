# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import tempfile
import time

import pytest
import requests

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
    start_worker,
)
from tests.mock_agent_helpers import get_session_row

_mock_sdks_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "mock_sdks")
)
_executors_dir = os.path.join(_mock_sdks_dir, "executors")


def _poll_until(predicate, timeout=30, interval=0.3):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _state_change_sequence(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? "
            "AND event_type = 'state_change' ORDER BY id",
            (session_id,),
        ).fetchall()
    return [json.loads(p).get("executor_state") for (p,) in rows]


def _has_idle_then_running(db_url, session_id):
    seq = _state_change_sequence(db_url, session_id)
    if "idle" not in seq:
        return False
    return "running" in seq[seq.index("idle") + 1 :]


def _executor_state(db_url, session_id):
    return get_session_row(db_url, session_id).get("executor_state")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_autoresume_turn_flips_display_status_to_working(test_database):
    assert os.path.exists(os.path.join(_executors_dir, "claude-executor.js")), (
        "mock_sdks/executors not built -- run 'make mock-sdks' first"
    )

    gate_dir = tempfile.mkdtemp(prefix="autoresume-gate-")
    gate_file = os.path.join(gate_dir, "release")

    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": "1"}
    ) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="autoresume-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial turn"
        )

        worker = start_worker(
            ctx["url"],
            interval="500ms",
            extra_env={
                "AGENTBEACON_EXECUTORS_DIR": _executors_dir,
                "NODE_OPTIONS": "--preserve-symlinks --preserve-symlinks-main",
                "AGENTBEACON_MOCK_SDK_AUTORESUME": "1",
                "AGENTBEACON_MOCK_SDK_AUTORESUME_GATE_FILE": gate_file,
            },
        )
        try:
            assert _poll_until(
                lambda: "idle" in _state_change_sequence(ctx["db_url"], session_id),
                timeout=30,
            ), "turn 1 did not settle to idle"
            t0 = time.monotonic()

            hold_s = 1
            margin_s = 7
            assert _poll_until(
                lambda: _has_idle_then_running(ctx["db_url"], session_id),
                timeout=hold_s + margin_s,
            ), (
                "auto-resume idle->running not observed within "
                f"{hold_s + margin_s}s of turn 1 settling "
                f"(elapsed {time.monotonic() - t0:.1f}s)"
            )

            resp = requests.get(
                f"{ctx['url']}/api/v1/sessions/{session_id}", timeout=10
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "working", (
                f"expected working during resume turn, got {resp.json()['status']}"
            )
            assert _executor_state(ctx["db_url"], session_id) == "running"

            open(gate_file, "w").close()

            def _settled_idle():
                if _executor_state(ctx["db_url"], session_id) != "idle":
                    return False
                r = requests.get(
                    f"{ctx['url']}/api/v1/sessions/{session_id}", timeout=10
                )
                return r.status_code == 200 and r.json()["status"] == "idle"

            assert _poll_until(_settled_idle, timeout=30), (
                "session status did not return to idle"
            )

            time.sleep(0.5)
            assert worker.poll() is None, "Worker should still be running"
        finally:
            cleanup_processes([worker])
