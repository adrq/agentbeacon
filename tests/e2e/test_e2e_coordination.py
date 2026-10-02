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


def _event_payloads(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _has_marker(db_url, session_id, marker_text):
    payloads = _event_payloads(db_url, session_id)
    return any(marker_text in p for p in payloads)


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
            "SELECT desired, executor_state, outcome, command_token FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if not row or row[2] is not None:
        return False
    with db_conn(db_url) as conn2:
        pending = conn2.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    is_active = row[1] == "running" or pending > 0 or row[3] is not None
    return not is_active


def _session_outcome(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT outcome FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return row[0] if row else None


def _execution_is_not_active(db_url, exec_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT desired, outcome FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
    if not row or row[1] is not None:
        return False
    with db_conn(db_url) as conn2:
        sessions = conn2.execute(
            "SELECT id, executor_state, outcome, command_token FROM sessions WHERE execution_id = ?",
            (exec_id,),
        ).fetchall()
        for s in sessions:
            if s[2] is not None:
                continue
            pending = conn2.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (s[0],)
            ).fetchone()[0]
            if s[1] == "running" or pending > 0 or s[3] is not None:
                return False
    return True


def _execution_outcome(db_url, exec_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_end_turn(test_database):
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        lead_id = seed_acp_scenario_agent(
            ctx["db_url"], "lead", "delegate", delegate_to="child-agent"
        )
        child_id = seed_acp_scenario_agent(ctx["db_url"], "child-agent", "end-turn")

        exec_id, lead_sid = create_execution_via_api(
            ctx["url"],
            lead_id,
            "Coordinate a task",
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
            assert len(children) == 1, (
                f"Expected exactly 1 child session, got {len(children)}"
            )
            child_sid = children[0][0]
            child_exec_id = children[0][4]

            assert _poll_until(
                lambda: _session_is_not_active(ctx["db_url"], child_sid),
            ), "Child session still active after end_turn"

            assert _poll_until(
                lambda: _has_marker(ctx["db_url"], lead_sid, "DELEGATE_PHASE_1_ACK"),
            ), (
                "Lead did not process turn-complete result (DELEGATE_PHASE_1_ACK not found)"
            )

            assert child_exec_id == exec_id, (
                f"Child execution_id {child_exec_id} != lead {exec_id}"
            )

            assert _poll_until(
                lambda: _execution_is_not_active(ctx["db_url"], exec_id),
                timeout=10,
            ), "Execution still has active sessions after lead processed turn-complete"

        finally:
            cleanup_processes([worker1, worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_multiple(test_database):
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        lead_id = seed_acp_scenario_agent(
            ctx["db_url"],
            "lead",
            "delegate-multi",
            delegate_to="child-agent",
            delegate_count=2,
        )
        child_id = seed_acp_scenario_agent(ctx["db_url"], "child-agent", "end-turn")

        exec_id, lead_sid = create_execution_via_api(
            ctx["url"],
            lead_id,
            "Coordinate multiple tasks",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_id),
            )
            conn.commit()

        worker1 = start_worker(ctx["url"], interval="500ms")
        worker2 = start_worker(ctx["url"], interval="500ms")
        worker3 = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 2,
            ), (
                f"Expected 2 children, got {len(_child_sessions(ctx['db_url'], lead_sid))}"
            )

            children = _child_sessions(ctx["db_url"], lead_sid)
            assert len(children) == 2, (
                f"Expected exactly 2 child sessions, got {len(children)}"
            )

            for child_row in children:
                child_id = child_row[0]
                assert _poll_until(
                    lambda cid=child_id: _session_is_not_active(ctx["db_url"], cid),
                ), f"Child {child_id} still active after end_turn"

            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], lead_sid, "DELEGATE_MULTI_PHASE_2_ACK"
                ),
            ), "Lead did not process all turn-complete results"

            assert _has_marker(ctx["db_url"], lead_sid, "DELEGATE_MULTI_PHASE_1_ACK"), (
                "Lead missing DELEGATE_MULTI_PHASE_1_ACK"
            )

            for child_id, _, _, _, child_exec_id in children:
                assert child_exec_id == exec_id

        finally:
            cleanup_processes([worker1, worker2, worker3])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_escalate_round_trip(test_database):
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        lead_id = seed_acp_scenario_agent(
            ctx["db_url"], "lead", "delegate-ask", delegate_to="child-agent"
        )
        child_id = seed_acp_scenario_agent(ctx["db_url"], "child-agent", "end-turn")

        exec_id, lead_sid = create_execution_via_api(
            ctx["url"],
            lead_id,
            "Coordinate then ask",
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
            assert len(children) == 1, (
                f"Expected exactly 1 child session, got {len(children)}"
            )
            child_sid = children[0][0]

            assert _poll_until(
                lambda: _session_is_not_active(ctx["db_url"], child_sid),
            ), "Child still active after end_turn"

            assert _poll_until(
                lambda: _execution_is_not_active(ctx["db_url"], exec_id),
            ), "Execution still has active sessions after lead escalated"

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{lead_sid}/message",
                json={"parts": [{"text": "Yes, approved"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"Answer submission failed: {resp.text}"

            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], lead_sid, "DELEGATE_ASK_PHASE_2_ACK"
                ),
            ), "Lead did not process user answer"

            assert _poll_until(
                lambda: _execution_is_not_active(ctx["db_url"], exec_id),
                timeout=10,
            ), "Execution still active after lead processed answer"

        finally:
            cleanup_processes([worker1, worker2])
