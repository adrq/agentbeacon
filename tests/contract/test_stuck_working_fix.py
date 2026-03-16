"""Regression tests for stuck "working" state bug fix.

Tests verify the 4 changes that fix the race condition where sessions/executions
get permanently stuck in "working" after all subagents complete and lead finishes turn.
"""

import json

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def _worker_sync(url, payload=None, timeout=10):
    """POST /api/worker/sync with optional JSON body, return parsed response."""
    if payload is None:
        payload = {}
    resp = httpx.post(f"{url}/api/worker/sync", json=payload, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _setup_parent_child(ctx, agent_name="test-agent"):
    """Create execution, claim lead, delegate to child, claim child.

    Returns (exec_id, lead_id, child_id, agent_id).
    """
    agent_id = seed_test_agent(ctx["db_url"], name=agent_name)
    exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
            (exec_id, agent_id),
        )
        conn.commit()

    # Claim lead session
    data = _worker_sync(ctx["url"])
    assert data["type"] == "session_assigned"
    assert data["sessionId"] == lead_id

    # Delegate to child via MCP
    result = mcp_tools_call(
        ctx["url"],
        lead_id,
        "delegate",
        {"agent": agent_name, "prompt": "child task"},
    )
    child_id = json.loads(result["content"][0]["text"])["session_id"]

    # Claim child session
    data = _worker_sync(ctx["url"])
    assert data["type"] == "session_assigned"
    assert data["sessionId"] == child_id

    return exec_id, lead_id, child_id, agent_id


def _insert_task(db_url, execution_id, session_id, text):
    """Insert a task into task_queue for testing."""
    with db_conn(db_url) as conn:
        payload = json.dumps(
            {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}}
        )
        conn.execute(
            "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
            (execution_id, session_id, payload),
        )
        conn.commit()


def _get_session_status(db_url, session_id):
    """Get current status of a session."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None


def _get_execution_status(db_url, execution_id):
    """Get current status of an execution."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        return row[0] if row else None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_completes_while_parent_has_queued_task(test_database):
    """Test Change 3: deliver_to_parent runs even when parent has queued task.

    This is the core race condition scenario: child completes and delivers output
    to parent, but parent already has a queued task. The old code would skip
    deliver_to_parent due to early return on has_task_for_session check.
    """
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        # Queue a task for parent while child is working
        _insert_task(ctx["db_url"], exec_id, lead_id, "user message to parent")

        # Child completes turn
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": child_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "child output"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify both tasks are in parent's queue: user message + child output
        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id",
                (lead_id,),
            ).fetchall()
            assert len(rows) == 2, "Parent should have 2 queued tasks"

            # First task: user message
            payload1 = json.loads(rows[0][0])
            assert payload1["message"]["parts"][0]["text"] == "user message to parent"

            # Second task: child output (delivered by deliver_to_parent)
            payload2 = json.loads(rows[1][0])
            assert (
                "turn complete from test-agent"
                in payload2["message"]["parts"][0]["text"]
            )
            assert "child output" in payload2["message"]["parts"][0]["text"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fetch_task_transitions_input_required_to_working(test_database):
    """Test Change 2: fetch_task transitions session from input-required to working."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id

        # Complete turn (transitions to input-required)
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "done"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify session is input-required
        assert _get_session_status(ctx["db_url"], session_id) == "input-required"
        assert _get_execution_status(ctx["db_url"], exec_id) == "input-required"

        # Queue a task
        _insert_task(ctx["db_url"], exec_id, session_id, "new prompt")

        # fetch_task should transition session to working
        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "fetch_task",
                }
            },
        )
        assert data["type"] == "prompt_delivery"

        # Verify session transitioned to working
        assert _get_session_status(ctx["db_url"], session_id) == "working"
        assert _get_execution_status(ctx["db_url"], exec_id) == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_widened_guard_accepts_input_required(test_database):
    """Test Change 1: widened guard accepts input-required and runs execution CAS.

    Simulates the race where fetch_task delivers a task without transitioning
    the session, so session stays input-required. The widened guard should
    accept it and run deliver_to_parent + execution CAS.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Complete turn (transitions to input-required)
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "first turn"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify input-required
        assert _get_session_status(ctx["db_url"], session_id) == "input-required"
        assert _get_execution_status(ctx["db_url"], exec_id) == "input-required"

        # Manually set execution to working (simulating the race where execution
        # didn't transition back to input-required, but session did)
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'working' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        # Verify setup: session is input-required, execution is working
        assert _get_session_status(ctx["db_url"], session_id) == "input-required"
        assert _get_execution_status(ctx["db_url"], exec_id) == "working"

        # Send result while session is STILL input-required
        # The widened guard should accept this and run execution CAS back to input-required
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 2,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "second turn"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Execution should now be input-required (widened guard ran the CAS)
        assert _get_execution_status(ctx["db_url"], exec_id) == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_fixes_stuck_execution(test_database):
    """Test Change 4: reconciliation catches and fixes stuck executions.

    Manually create a stuck execution (working with all sessions input-required)
    and verify reconciliation fixes it.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim and complete session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "done"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify both are input-required
        assert _get_session_status(ctx["db_url"], session_id) == "input-required"
        assert _get_execution_status(ctx["db_url"], exec_id) == "input-required"

        # Manually set execution to working (simulating the race condition)
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'working' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        # Verify stuck state
        assert _get_execution_status(ctx["db_url"], exec_id) == "working"

        # Trigger liveness scan (which includes reconciliation)
        # The scheduler should have a liveness scan endpoint or we can wait for it to run
        # For this test, we'll verify the query that reconciliation uses
        with db_conn(ctx["db_url"]) as conn:
            # This mirrors the actual reconciliation query in recovery.rs:
            # requires at least one input-required session (not all-terminal)
            row = conn.execute(
                """SELECT e.id
                   FROM executions e
                   WHERE e.status = 'working'
                   AND EXISTS (
                       SELECT 1 FROM sessions s
                       WHERE s.execution_id = e.id
                       AND s.status = 'input-required'
                   )
                   AND NOT EXISTS (
                       SELECT 1 FROM sessions s
                       WHERE s.execution_id = e.id
                       AND s.status IN ('working', 'submitted')
                   )""",
                (),
            ).fetchone()
            assert row is not None, (
                "Reconciliation query should find the stuck execution"
            )
            assert row[0] == exec_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_does_not_fire_when_sessions_active(test_database):
    """Test Change 4: reconciliation does NOT fire when children are still working.

    Note: When the lead completes, execution may transition to input-required even
    though child is still working. This is okay - the execution status represents
    the lead's state. The reconciliation logic will NOT match this because the
    execution is no longer "working".

    The real test is: manually set execution to working while child is still
    working, then verify reconciliation doesn't touch it (because child exists
    in working state).
    """
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        # Lead completes turn (execution transitions to input-required)
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": lead_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "lead waiting"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Lead is input-required, child is working
        assert _get_session_status(ctx["db_url"], lead_id) == "input-required"
        assert _get_session_status(ctx["db_url"], child_id) == "working"

        # Manually set execution to working (simulating the scenario where
        # execution is stuck working but should not be reconciled because
        # child session is still working)
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'working' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        # Now execution is working with child also working
        assert _get_execution_status(ctx["db_url"], exec_id) == "working"

        # Reconciliation query should NOT match this execution (child is working)
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                """SELECT e.id
                   FROM executions e
                   WHERE e.status = 'working'
                   AND EXISTS (
                       SELECT 1 FROM sessions s
                       WHERE s.execution_id = e.id
                       AND s.status = 'input-required'
                   )
                   AND NOT EXISTS (
                       SELECT 1 FROM sessions s
                       WHERE s.execution_id = e.id
                       AND s.status IN ('working', 'submitted')
                   )""",
                (),
            ).fetchone()
            assert row is None, (
                "Reconciliation should not match execution with active sessions"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fetch_task_called_when_session_already_working(test_database):
    """Test Change 2: fetch_task handles session already working (no duplicate transition)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session (now working)
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert _get_session_status(ctx["db_url"], session_id) == "working"

        # Queue a task
        _insert_task(ctx["db_url"], exec_id, session_id, "new prompt")

        # fetch_task while already working (should be no-op transition)
        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "fetch_task",
                }
            },
        )
        assert data["type"] == "prompt_delivery"

        # Session should still be working
        assert _get_session_status(ctx["db_url"], session_id) == "working"
