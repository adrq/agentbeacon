"""E2E coordination tests: delegation, handoff, and ask_user round-trips.

Proves the full master→child→master flow through real scheduler + worker
processes using mock ACP agents with scripted coordination scenarios.

Key constraint: each worker handles ONE session at a time, so delegation
tests require 2+ workers (master on worker 1, child on worker 2).

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
def test_e2e_delegate_handoff(test_database):
    """Full master→child→master round-trip through real processes."""
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        master_id = seed_acp_scenario_agent(
            ctx["db_url"], "master", "delegate", delegate_to="child-agent"
        )
        seed_acp_scenario_agent(ctx["db_url"], "child-agent", "handoff")

        exec_id, master_sid = create_execution_via_api(
            ctx["url"],
            master_id,
            "Coordinate a task",
        )

        worker1 = start_worker(ctx["url"], interval="500ms")
        worker2 = start_worker(ctx["url"], interval="500ms")
        try:
            # 1. Exactly one child session created
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], master_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], master_sid)
            assert len(children) == 1, (
                f"Expected exactly 1 child session, got {len(children)}"
            )
            child_sid = children[0][0]
            child_exec_id = children[0][2]

            # 2. Child completes (handoff)
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "completed",
            ), (
                f"Child session did not complete, status={_session_status(ctx['db_url'], child_sid)}"
            )

            # 3. Master processed handoff result (full round-trip)
            assert _poll_until(
                lambda: _has_marker(ctx["db_url"], master_sid, "DELEGATE_PHASE_1_ACK"),
            ), "Master did not process handoff result (DELEGATE_PHASE_1_ACK not found)"

            # 4. Session tree is correct
            assert child_exec_id == exec_id, (
                f"Child execution_id {child_exec_id} != master {exec_id}"
            )

            # 5. Execution still working (master alive after processing)
            assert _poll_until(
                lambda: _execution_status(ctx["db_url"], exec_id) == "working",
                timeout=10,
            ), (
                f"Expected execution working, got {_execution_status(ctx['db_url'], exec_id)}"
            )

        finally:
            cleanup_processes([worker1, worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_multiple(test_database):
    """Multiple child delegations; results delivered independently."""
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        master_id = seed_acp_scenario_agent(
            ctx["db_url"],
            "master",
            "delegate-multi",
            delegate_to="child-agent",
            delegate_count=2,
        )
        seed_acp_scenario_agent(ctx["db_url"], "child-agent", "handoff")

        exec_id, master_sid = create_execution_via_api(
            ctx["url"],
            master_id,
            "Coordinate multiple tasks",
        )

        worker1 = start_worker(ctx["url"], interval="500ms")
        worker2 = start_worker(ctx["url"], interval="500ms")
        try:
            # 1. Exactly two child sessions created
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], master_sid)) >= 2,
            ), (
                f"Expected 2 children, got {len(_child_sessions(ctx['db_url'], master_sid))}"
            )

            children = _child_sessions(ctx["db_url"], master_sid)
            assert len(children) == 2, (
                f"Expected exactly 2 child sessions, got {len(children)}"
            )

            # 2. Both children complete
            for child_id, _, _ in children:
                assert _poll_until(
                    lambda cid=child_id: _session_status(ctx["db_url"], cid)
                    == "completed",
                ), f"Child {child_id} did not complete"

            # 3. Master processed all handoff results
            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], master_sid, "DELEGATE_MULTI_PHASE_2_ACK"
                ),
            ), "Master did not process all handoff results"

            assert _has_marker(
                ctx["db_url"], master_sid, "DELEGATE_MULTI_PHASE_1_ACK"
            ), "Master missing DELEGATE_MULTI_PHASE_1_ACK"

            # 4. All children have correct parent and execution
            for child_id, _, child_exec_id in children:
                assert child_exec_id == exec_id

        finally:
            cleanup_processes([worker1, worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_ask_user_round_trip(test_database):
    """Delegate → child completes → master asks user → user answers → master continues."""
    db_url = test_database

    with scheduler_context(db_url=db_url) as ctx:
        master_id = seed_acp_scenario_agent(
            ctx["db_url"], "master", "delegate-ask", delegate_to="child-agent"
        )
        seed_acp_scenario_agent(ctx["db_url"], "child-agent", "handoff")

        exec_id, master_sid = create_execution_via_api(
            ctx["url"],
            master_id,
            "Coordinate then ask",
        )

        worker1 = start_worker(ctx["url"], interval="500ms")
        worker2 = start_worker(ctx["url"], interval="500ms")
        try:
            # 1. Exactly one child created, then completes
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], master_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], master_sid)
            assert len(children) == 1, (
                f"Expected exactly 1 child session, got {len(children)}"
            )
            child_sid = children[0][0]

            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "completed",
            ), "Child did not complete"

            # 2. Execution goes input-required (master called ask_user)
            assert _poll_until(
                lambda: _execution_status(ctx["db_url"], exec_id) == "input-required",
            ), (
                f"Execution did not go input-required, status={_execution_status(ctx['db_url'], exec_id)}"
            )

            # 3. Submit answer
            resp = httpx.post(
                f"{ctx['url']}/api/sessions/{master_sid}/message",
                json={"message": "Yes, approved"},
                timeout=10,
            )
            assert resp.status_code == 200, f"Answer submission failed: {resp.text}"

            # 4. Master processes answer (full round-trip)
            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], master_sid, "DELEGATE_ASK_PHASE_2_ACK"
                ),
            ), "Master did not process user answer"

            # 5. Execution returned to working
            assert _poll_until(
                lambda: _execution_status(ctx["db_url"], exec_id) == "working",
                timeout=10,
            ), (
                f"Expected execution working after answer, got {_execution_status(ctx['db_url'], exec_id)}"
            )

        finally:
            cleanup_processes([worker1, worker2])
