"""E2E coordination tests: delegation, turn-complete, and escalate round-trips.

Proves the full lead→child→lead flow through real scheduler + worker
processes using mock ACP agents with scripted coordination scenarios.

Key constraint: each worker handles ONE session at a time, so delegation
tests require 2+ workers (lead on worker 1, child on worker 2).

Run with: uv run pytest tests/e2e/test_e2e_coordination.py -v
"""

import time

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_acp_scenario_agent,
    start_worker,
)


def _poll_until(predicate, timeout=60, interval=0.5):
    """Poll until predicate returns truthy or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return False


def _event_payloads(db_url, session_id):
    """Return all event payload strings for a session, ordered by created_at."""
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _has_marker(db_url, session_id, marker_text):
    """Check if any event payload for session contains the marker text."""
    payloads = _event_payloads(db_url, session_id)
    return any(marker_text in p for p in payloads)


def _child_sessions(db_url, parent_session_id):
    """Return child session rows as list of (id, status, execution_id)."""
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT id, status, execution_id FROM sessions WHERE parent_session_id = ?",
            (parent_session_id,),
        ).fetchall()
    return rows


def _session_status(db_url, session_id):
    """Read session status."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None


def _execution_status(db_url, exec_id):
    """Read execution status."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM executions WHERE id = ?",
            (exec_id,),
        ).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_end_turn(test_database):
    """Full lead→child→lead round-trip through real processes.

    Child uses end-turn scenario (goes input-required), system delivers
    turn-complete notification to lead.
    """
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
            # 1. Exactly one child session created
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], lead_sid)
            assert len(children) == 1, (
                f"Expected exactly 1 child session, got {len(children)}"
            )
            child_sid = children[0][0]
            child_exec_id = children[0][2]

            # 2. Child goes input-required (end_turn)
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "input-required",
            ), (
                f"Child session did not go input-required, status={_session_status(ctx['db_url'], child_sid)}"
            )

            # 3. Lead processed turn-complete notification (full round-trip)
            assert _poll_until(
                lambda: _has_marker(ctx["db_url"], lead_sid, "DELEGATE_PHASE_1_ACK"),
            ), (
                "Lead did not process turn-complete result (DELEGATE_PHASE_1_ACK not found)"
            )

            # 4. Session tree is correct
            assert child_exec_id == exec_id, (
                f"Child execution_id {child_exec_id} != lead {exec_id}"
            )

            # 5. Execution idle after lead processed turn-complete
            assert _poll_until(
                lambda: _execution_status(ctx["db_url"], exec_id) == "input-required",
                timeout=10,
            ), (
                f"Expected execution input-required, got {_execution_status(ctx['db_url'], exec_id)}"
            )

        finally:
            cleanup_processes([worker1, worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_multiple(test_database):
    """Multiple child delegations; turn-complete results delivered independently."""
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
            # 1. Exactly two child sessions created
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 2,
            ), (
                f"Expected 2 children, got {len(_child_sessions(ctx['db_url'], lead_sid))}"
            )

            children = _child_sessions(ctx["db_url"], lead_sid)
            assert len(children) == 2, (
                f"Expected exactly 2 child sessions, got {len(children)}"
            )

            # 2. Both children go input-required (end-turn)
            for child_id, _, _ in children:
                assert _poll_until(
                    lambda cid=child_id: _session_status(ctx["db_url"], cid)
                    == "input-required",
                ), f"Child {child_id} did not go input-required"

            # 3. Lead processed all turn-complete results
            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], lead_sid, "DELEGATE_MULTI_PHASE_2_ACK"
                ),
            ), "Lead did not process all turn-complete results"

            assert _has_marker(ctx["db_url"], lead_sid, "DELEGATE_MULTI_PHASE_1_ACK"), (
                "Lead missing DELEGATE_MULTI_PHASE_1_ACK"
            )

            # 4. All children have correct parent and execution
            for child_id, _, child_exec_id in children:
                assert child_exec_id == exec_id

        finally:
            cleanup_processes([worker1, worker2, worker3])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_escalate_round_trip(test_database):
    """Delegate → child end-turn → lead escalates → user answers → lead continues."""
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
            # 1. Exactly one child created, then goes input-required
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], lead_sid)
            assert len(children) == 1, (
                f"Expected exactly 1 child session, got {len(children)}"
            )
            child_sid = children[0][0]

            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "input-required",
            ), "Child did not go input-required"

            # 2. Execution goes input-required (lead called escalate)
            assert _poll_until(
                lambda: _execution_status(ctx["db_url"], exec_id) == "input-required",
            ), (
                f"Execution did not go input-required, status={_execution_status(ctx['db_url'], exec_id)}"
            )

            # 3. Submit answer
            resp = httpx.post(
                f"{ctx['url']}/api/sessions/{lead_sid}/message",
                json={"message": "Yes, approved"},
                timeout=10,
            )
            assert resp.status_code == 200, f"Answer submission failed: {resp.text}"

            # 4. Lead processes answer (full round-trip)
            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], lead_sid, "DELEGATE_ASK_PHASE_2_ACK"
                ),
            ), "Lead did not process user answer"

            # 5. Execution idle after lead processed answer
            assert _poll_until(
                lambda: _execution_status(ctx["db_url"], exec_id) == "input-required",
                timeout=10,
            ), (
                f"Expected execution input-required after answer, got {_execution_status(ctx['db_url'], exec_id)}"
            )

        finally:
            cleanup_processes([worker1, worker2])
