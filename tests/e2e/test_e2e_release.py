"""E2E tests for release tool and cascade termination.

Proves the full lead→delegate→release flow through real scheduler + worker
processes using mock ACP agents with scripted coordination scenarios.

Run with: uv run pytest tests/e2e/test_e2e_release.py -v
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


def _has_marker(db_url, session_id, marker_text):
    """Check if any event payload for session contains the marker text."""
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return any(marker_text in r[0] for r in rows)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_delegate_release(test_database):
    """Full lead→delegate→release round-trip through real processes.

    Flow:
    1. Lead (delegate-release) delegates to child (end-turn)
    2. Child goes input-required (end_turn)
    3. Turn-complete auto-notification delivered to lead (phase 1 ack)
    4. Lead goes input-required after acknowledging notification
    5. User sends message to lead to trigger release phase (phase 2)
    6. Lead calls release on child → child → completed
    """
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
            # 1. Child session created
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], lead_sid)
            child_sid = children[0][0]

            # 2. Lead acknowledged turn-complete notification (phase 1)
            assert _poll_until(
                lambda: _has_marker(
                    ctx["db_url"], lead_sid, "RELEASE_PHASE_1_NOTIFY_ACK"
                ),
            ), "Lead did not acknowledge child turn-complete notification"

            # 3. Lead and child both at input-required
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], lead_sid) == "input-required",
            ), (
                f"Lead did not go input-required, status={_session_status(ctx['db_url'], lead_sid)}"
            )

            assert _session_status(ctx["db_url"], child_sid) == "input-required", (
                f"Child should be input-required, status={_session_status(ctx['db_url'], child_sid)}"
            )

            # 4. Send message to lead to trigger release phase
            resp = httpx.post(
                f"{ctx['url']}/api/sessions/{lead_sid}/message",
                json={"parts": [{"kind": "text", "text": "Release the child now"}]},
                timeout=10,
            )
            assert resp.status_code == 200

            # 5. Lead calls release → child transitions to completed
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "completed",
            ), (
                f"Child not completed after release, status={_session_status(ctx['db_url'], child_sid)}"
            )

            # 6. Lead processed release result
            assert _poll_until(
                lambda: _has_marker(ctx["db_url"], lead_sid, "RELEASE_PHASE_2_ACK"),
            ), "Lead did not emit RELEASE_PHASE_2_ACK"

        finally:
            cleanup_processes([worker1, worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_e2e_execution_cancel_cascades_delegation_tree(test_database):
    """Execution cancel with active delegation tree terminates all sessions.

    Regression test for the refactored execution cancel path using
    terminate_subtree(). Proves workers detect terminal states and clean up.
    """
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
            # Wait for delegation tree to form
            assert _poll_until(
                lambda: len(_child_sessions(ctx["db_url"], lead_sid)) >= 1,
            ), "Child session was not created"

            children = _child_sessions(ctx["db_url"], lead_sid)
            child_sid = children[0][0]

            # Wait for child to be active
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "input-required",
            ), "Child did not go input-required"

            # Cancel the execution
            resp = httpx.post(
                f"{ctx['url']}/api/executions/{exec_id}/cancel",
                timeout=10,
            )
            assert resp.status_code == 200

            # All sessions should be canceled
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], lead_sid) == "canceled",
            ), f"Lead not canceled, status={_session_status(ctx['db_url'], lead_sid)}"

            assert _poll_until(
                lambda: _session_status(ctx["db_url"], child_sid) == "canceled",
            ), f"Child not canceled, status={_session_status(ctx['db_url'], child_sid)}"

            # Execution itself is canceled
            assert _execution_status(ctx["db_url"], exec_id) == "canceled"

        finally:
            cleanup_processes([worker1, worker2])
