"""Contract tests for non-destructive long-poll + fetch_task protocol (KI-73 fix).

The long-poll now returns task_available (non-destructive peek) instead of
prompt_delivery. The worker must send a follow-up fetch_task sync to pop the
task. This eliminates the biased-select race where a dropped long-poll response
would permanently lose a queued task.
"""

import json
import threading
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
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


def _task_queue_count(db_url, session_id):
    """Count tasks in queue for a session."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0]


def _insert_task(db_url, execution_id, session_id, text="test task"):
    """Insert a task directly into task_queue."""
    payload = json.dumps(
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        }
    )
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
            (execution_id, session_id, payload),
        )
        conn.commit()


def _setup_working_session(ctx):
    """Create execution, claim session via idle sync, return (exec_id, session_id)."""
    agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

    data = _worker_sync(ctx["url"])
    assert data["type"] == "session_assigned"
    assert data["sessionId"] == session_id

    return exec_id, session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fetch_task_returns_prompt_delivery(test_database):
    """fetch_task pops a queued task and returns prompt_delivery."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_working_session(ctx)

        _insert_task(ctx["db_url"], exec_id, session_id, "fetch me")

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
        assert data["sessionId"] == session_id
        assert data["task"]["taskPayload"]["message"]["parts"][0]["text"] == "fetch me"

        # Queue should be empty after fetch
        assert _task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fetch_task_empty_queue_returns_no_action(test_database):
    """fetch_task with no queued task returns no_action."""
    with scheduler_context(db_url=test_database) as ctx:
        _, session_id = _setup_working_session(ctx)

        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "fetch_task",
                }
            },
        )

        assert data["type"] == "no_action"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fetch_task_terminated_session_returns_session_complete(test_database):
    """fetch_task for completed session returns session_complete."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_working_session(ctx)

        # Mark session as completed
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        # Insert a task (should be ignored since session is terminal)
        _insert_task(ctx["db_url"], exec_id, session_id, "should not deliver")

        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "fetch_task",
                }
            },
        )

        assert data["type"] == "session_complete"
        assert data["sessionId"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_long_poll_returns_task_available_not_prompt_delivery(test_database):
    """Long-poll returns task_available (non-destructive) when task arrives."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_working_session(ctx)

        # Insert task before long-poll
        _insert_task(ctx["db_url"], exec_id, session_id, "peek me")

        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "waiting_for_event",
                }
            },
        )

        assert data["type"] == "task_available"
        assert data["sessionId"] == session_id

        # Task should still be in queue (non-destructive peek)
        assert _task_queue_count(ctx["db_url"], session_id) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_available_then_fetch_task_round_trip(test_database):
    """Full round-trip: long-poll wakes with task_available, fetch_task pops."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_working_session(ctx)

        # Set session to input-required so we can push via the message API
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'input-required' WHERE id = ?",
                (session_id,),
            )
            conn.execute(
                "UPDATE executions SET status = 'input-required' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        # Start long-poll in background thread
        result_holder = [None]

        def worker_wait():
            result_holder[0] = _worker_sync(
                ctx["url"],
                {
                    "sessionState": {
                        "sessionId": session_id,
                        "status": "waiting_for_event",
                    }
                },
                timeout=40,
            )

        t = threading.Thread(target=worker_wait)
        t.start()

        # Give time for the long-poll to enter the wait loop
        time.sleep(1.0)

        # Push via API (triggers notify_waiters)
        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "round trip"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        t.join(timeout=10)
        assert not t.is_alive(), "Worker thread should have returned"

        # Long-poll should return task_available
        data = result_holder[0]
        assert data["type"] == "task_available"
        assert data["sessionId"] == session_id

        # Task still in queue
        assert _task_queue_count(ctx["db_url"], session_id) == 1

        # Now fetch_task pops it
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
        assert (
            data["task"]["taskPayload"]["message"]["parts"][0]["text"] == "round trip"
        )

        # Queue empty
        assert _task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sync_with_result_returns_task_available_not_prompt_delivery(test_database):
    """sync-with-result returns task_available (non-destructive) when task is queued."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_working_session(ctx)

        # Queue a task before the result sync
        _insert_task(ctx["db_url"], exec_id, session_id, "queued during turn")

        # Send sync with session_result (simulating TurnComplete)
        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "running",
                },
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
                },
            },
        )

        assert data["type"] == "task_available"
        assert data["sessionId"] == session_id

        # Task still in queue (non-destructive)
        assert _task_queue_count(ctx["db_url"], session_id) == 1

        # Now fetch_task pops it
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
        assert (
            data["task"]["taskPayload"]["message"]["parts"][0]["text"]
            == "queued during turn"
        )

        # Queue empty
        assert _task_queue_count(ctx["db_url"], session_id) == 0
