# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    get_session_row,
    get_task_queue_payload,
    make_executor_report,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


def _wait_for_recovery(url, db_url, session_id, timeout=5, interval=0.15):
    baseline = get_session_row(db_url, session_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = get_session_row(db_url, session_id)
        if current["executor_state"] != baseline["executor_state"]:
            return current
        if current.get("outcome") is not None and baseline.get("outcome") is None:
            return current
        post_worker_sync(url, worker_id="reconcile-trigger")
        time.sleep(interval)
    return get_session_row(db_url, session_id)


def _wait_for_task_payload(url, db_url, session_id, timeout=5, interval=0.15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = get_task_queue_payload(db_url, session_id)
        if payload is not None:
            return payload
        post_worker_sync(url, worker_id="reconcile-trigger")
        time.sleep(interval)
    return None


def _setup_crashed_session(ctx, agent_type="claude_sdk"):
    agent_id = seed_test_agent(
        ctx["db_url"], name=f"test-{agent_type}", agent_type=agent_type
    )
    exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test prompt")
    post_worker_sync(ctx["url"], "w1")
    set_session_fields(
        ctx["db_url"],
        lead_sid,
        agent_session_id="sdk-session-abc",
        cwd="/tmp/test-workspace",
    )
    post_worker_sync(
        ctx["url"],
        "w1",
        executor_report=make_executor_report(lead_sid, "crashed"),
    )
    return agent_id, exec_id, lead_sid


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_resubmits_crashed_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["executor_state"] == "unassigned"
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_resubmits_idle_then_crashed_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        post_worker_sync(ctx["url"], "w1")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            agent_session_id="sdk-session-abc",
            cwd="/tmp/test-workspace",
            executor_state="idle",
            worker_id="w1",
        )
        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["executor_state"] == "unassigned"
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_session_without_agent_session_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="claude-no-sid", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        post_worker_sync(ctx["url"], "w1")
        set_session_fields(ctx["db_url"], lead_sid, cwd="/tmp/workspace")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["outcome"] == "failed"
        assert session["desired"] == "terminate"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_acp_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx, agent_type="acp")

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["outcome"] == "failed"
        assert session["desired"] == "terminate"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_budget_exhausted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)
        set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=2)

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["outcome"] == "failed"
        assert session["desired"] == "terminate"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_terminal_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_crashed_session(ctx)
        set_execution_fields(
            ctx["db_url"],
            exec_id,
            desired="terminate",
            outcome="completed",
        )

        post_worker_sync(ctx["url"], worker_id="probe-worker")

        session = get_session_row(ctx["db_url"], lead_sid)
        assert session["executor_state"] == "crashed"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_assign_has_resume(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)

        resp = post_worker_sync(ctx["url"], "w1")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["session_id"] == lead_sid
        assert resp["action"].get("resume") is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_assign_has_driver_info(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)

        resp = post_worker_sync(ctx["url"], "w1")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        action = resp["action"]
        assert action.get("driver", {}).get("platform") == "claude_sdk"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_increments_counter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_state_change_event_logged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_crashed_session(ctx)

        _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT payload FROM events WHERE execution_id = ? AND event_type = 'state_change' AND session_id = ? ORDER BY id DESC LIMIT 1",
                (exec_id, lead_sid),
            ).fetchone()
        assert row is not None, "no state_change event found"
        payload = json.loads(row[0])
        assert payload["executor_state"] == "unassigned"
        assert payload["recovery_attempt"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_double_crash_recovers_again(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["executor_state"] == "unassigned"
        assert session["recovery_attempts"] == 1

        set_session_fields(
            ctx["db_url"],
            lead_sid,
            executor_state="running",
            agent_session_id="sdk-session-def",
            cwd="/tmp/test-workspace",
            worker_id="w1",
        )
        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["executor_state"] == "unassigned"
        assert session["recovery_attempts"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_assign_uses_session_cwd(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)

        resp = post_worker_sync(ctx["url"], "w1")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"].get("cwd") == "/tmp/test-workspace"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_session_without_cwd(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="claude-no-cwd", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        post_worker_sync(ctx["url"], "w1")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            agent_session_id="sdk-session-abc",
            cwd=None,
        )
        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["outcome"] == "failed"
        assert session["desired"] == "terminate"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_atomicity_state_and_task(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, lead_sid = _setup_crashed_session(ctx)

        session = _wait_for_recovery(ctx["url"], ctx["db_url"], lead_sid)
        assert session["executor_state"] == "unassigned"
        row = get_session_row(ctx["db_url"], lead_sid)
        assert row["recovery_attempts"] == 1, "recovery_attempts not incremented"
