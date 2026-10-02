# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    start_worker,
)
from tests.mock_agent_helpers import seed_acp_scenario_agent


def _poll_until(predicate, timeout=30, interval=0.5):
    start = time.time()
    while time.time() - start < timeout:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return False


def _child_sessions(db_url, parent_session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT id, desired, executor_state, outcome, execution_id FROM sessions WHERE parent_session_id = ?",
            (parent_session_id,),
        ).fetchall()
    return rows


def _session_is_not_active(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT executor_state, outcome, command_token FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if not row or row[1] is not None:
        return False
    with db_conn(db_url) as conn2:
        pending = conn2.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    return not (row[0] == "running" or pending > 0 or row[2] is not None)


def _session_outcome(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT outcome FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return row[0] if row else None


def _execution_outcome(db_url, exec_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
    return row[0] if row else None


def _has_marker(db_url, session_id, marker_text):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return any(marker_text in r[0] for r in rows)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_release(test_database):
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        lead_id = seed_acp_scenario_agent(
            ctx["db_url"], "lead", "delegate-release", delegate_to="idle-child"
        )
        child_id = seed_acp_scenario_agent(ctx["db_url"], "idle-child", "end-turn")

        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], lead_id, "Delegate then release"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_id),
            )
            conn.commit()

        worker1 = start_worker(ctx["url"], interval="500ms")
        worker2 = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], lead_sid)
            child_sid = children[0][0]

            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], lead_sid, "RELEASE_PHASE_1_NOTIFY_ACK"
                ),
            ), "Lead did not acknowledge child turn-complete notification"

            assert _poll_until(
                lambda: _session_is_not_active(ctx["db_url"], lead_sid),
            ), "Lead still active after processing notification"

            assert _session_is_not_active(ctx["db_url"], child_sid), (
                "Child should be non-active after end_turn"
            )

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{lead_sid}/message",
                json={"parts": [{"text": "Release the child now"}]},
                timeout=10,
            )
            assert resp.status_code == 200

            assert _poll_until(
                lambda: _session_outcome(ctx["db_url"], child_sid) == "completed",
            ), "Child not completed after release"

            assert _poll_until(
                lambda: _has_marker(ctx["db_url"], lead_sid, "RELEASE_PHASE_2_ACK"),
            ), "Lead did not emit RELEASE_PHASE_2_ACK"

        finally:
            cleanup_processes([worker1, worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_execution_cancel_cascades_delegation_tree(test_database):
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        lead_id = seed_acp_scenario_agent(
            ctx["db_url"], "lead", "delegate", delegate_to="idle-child"
        )
        child_id = seed_acp_scenario_agent(ctx["db_url"], "idle-child", "end-turn")

        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], lead_id, "Cancel tree test"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_id),
            )
            conn.commit()

        worker1 = start_worker(ctx["url"], interval="500ms")
        worker2 = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], lead_sid)
            child_sid = children[0][0]

            assert _poll_until(
                lambda: _session_is_not_active(ctx["db_url"], child_sid),
            ), "Child still active after end_turn"

            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate",
                timeout=10,
            )
            assert resp.status_code == 200

            assert _poll_until(
                lambda: _session_outcome(ctx["db_url"], lead_sid) is not None,
            ), (
                f"Lead not terminated, outcome={_session_outcome(ctx['db_url'], lead_sid)}"
            )

            assert _poll_until(
                lambda: _session_outcome(ctx["db_url"], child_sid) is not None,
            ), (
                f"Child not terminated, outcome={_session_outcome(ctx['db_url'], child_sid)}"
            )

            assert _execution_outcome(ctx["db_url"], exec_id) is not None

        finally:
            cleanup_processes([worker1, worker2])
