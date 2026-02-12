"""E2E tests: real scheduler + real worker + mock ACP agent.

Verifies the full worker executor flow using a real scheduler database,
a real worker binary, and the ACP mock agent subprocess.
"""

import json
import time

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_acp_mock_agent,
    seed_test_agent,
    start_worker,
)


def _poll_until(predicate, timeout=30, interval=0.5):
    """Poll until predicate returns True or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _session_agent_session_id(db_url, session_id):
    """Read agent_session_id from sessions table."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT agent_session_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None


def _session_status(db_url, session_id):
    """Read session status from DB."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None


def _mark_session_completed(db_url, session_id):
    """Directly mark session completed in DB.

    Bypasses scheduler handler invariants (event emission, status validation).
    Replace with API call when a session-complete endpoint exists.
    """
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        conn.commit()


def _force_input_required(db_url, exec_id, session_id):
    """Force session+execution to input-required for multi-turn testing.

    Bypasses scheduler handler invariants. The real system transitions
    via ask_user MCP tool; no external API exists for this state change.
    """
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE sessions SET status = 'input-required' WHERE id = ?",
            (session_id,),
        )
        conn.execute(
            "UPDATE executions SET status = 'input-required' WHERE id = ?",
            (exec_id,),
        )
        conn.commit()


def _agent_message_count(db_url, session_id):
    """Count message events with role=agent for a session."""
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = 'message'",
            (session_id,),
        ).fetchall()
    return sum(1 for (p,) in rows if json.loads(p).get("role") == "agent")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_executes_acp_session(test_database):
    """Worker picks up ACP session, executes it, and reports agent_session_id."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello from e2e test"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_agent_session_id(ctx["db_url"], session_id)
                is not None,
                timeout=30,
            ), "Worker did not report agent_session_id"

            assert _session_status(ctx["db_url"], session_id) == "working"

            _mark_session_completed(ctx["db_url"], session_id)

            time.sleep(3)
            assert worker.poll() is None, (
                "Worker should still be running after session_complete"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_multi_turn_acp_session(test_database):
    """Worker handles multi-turn: first prompt, then follow-up via scheduler API."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "first turn"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for first turn — agent message event emitted
            assert _poll_until(
                lambda: _agent_message_count(ctx["db_url"], session_id) >= 1,
                timeout=30,
            ), "Worker did not complete first turn (no agent message event)"
            assert _agent_message_count(ctx["db_url"], session_id) == 1

            first_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)

            # Set session to input-required so we can push via POST /api/sessions/{id}/message
            _force_input_required(ctx["db_url"], exec_id, session_id)

            # Push follow-up via scheduler API
            resp = httpx.post(
                f"{ctx['url']}/api/sessions/{session_id}/message",
                json={"message": "second turn prompt"},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            # Wait for second agent message event
            assert _poll_until(
                lambda: _agent_message_count(ctx["db_url"], session_id) >= 2,
                timeout=30,
            ), "Worker did not complete second turn (expected 2 agent message events)"
            assert _agent_message_count(ctx["db_url"], session_id) == 2

            # agent_session_id should be stable across turns
            second_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert first_agent_sid == second_agent_sid, (
                f"agent_session_id changed between turns: {first_agent_sid} != {second_agent_sid}"
            )

            # Complete session
            _mark_session_completed(ctx["db_url"], session_id)

            time.sleep(2)
            assert worker.poll() is None, "Worker should still be running"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_handles_session_complete_during_wait(test_database):
    """Worker returns to idle after session_complete and picks up next execution."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])

        exec_id_1, session_id_1 = create_execution_via_api(
            ctx["url"], agent_id, "first execution"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_agent_session_id(ctx["db_url"], session_id_1)
                is not None,
                timeout=30,
            ), "Worker did not complete first execution"

            _mark_session_completed(ctx["db_url"], session_id_1)

            exec_id_2, session_id_2 = create_execution_via_api(
                ctx["url"], agent_id, "second execution"
            )

            assert _poll_until(
                lambda: _session_agent_session_id(ctx["db_url"], session_id_2)
                is not None,
                timeout=60,
            ), "Worker did not pick up second execution after returning to idle"

            _mark_session_completed(ctx["db_url"], session_id_2)

            time.sleep(2)
            assert worker.poll() is None, "Worker should still be running"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_idle_no_sessions(test_database):
    """Worker stays alive when no sessions are available."""
    with scheduler_context(db_url=test_database) as ctx:
        worker = start_worker(ctx["url"], interval="500ms")
        try:
            time.sleep(3)
            assert worker.poll() is None, "Worker should stay alive with no sessions"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_bad_agent_config(test_database):
    """Worker doesn't crash on agent with nonexistent command."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"],
            name="bad-agent",
            agent_type="acp",
        )

        bad_config = json.dumps(
            {
                "command": "/nonexistent/path/to/agent",
                "args": [],
                "timeout": 5,
            }
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE agents SET config = ? WHERE id = ?",
                (bad_config, agent_id),
            )
            conn.commit()

        create_execution_via_api(ctx["url"], agent_id, "will fail")

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            time.sleep(5)
            assert worker.poll() is None, "Worker should not crash on bad agent config"
        finally:
            cleanup_processes([worker])
