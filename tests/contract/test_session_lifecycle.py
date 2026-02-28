"""Contract tests for Phase 2a session lifecycle — worker sync protocol.

Tests simulate a worker by making HTTP calls to /api/worker/sync.
No real worker binary needed.
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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_enqueues_task(test_database):
    """POST /api/executions creates task_queue row for the lead session."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth"
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT execution_id, session_id, task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None, "task_queue should have a row for the lead session"
        assert row[0] == exec_id
        assert row[1] == session_id

        payload = json.loads(row[2])
        assert payload["agent_id"] == agent_id
        assert payload["driver"]["platform"] == "claude_sdk"
        assert payload["message"]["parts"][0]["text"] == "implement auth"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_assigns_sdk_session(test_database):
    """Idle worker sync finds and assigns an sdk+submitted session."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

        # Idle worker sync — empty body
        data = _worker_sync(ctx["url"])

        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id
        assert data["task"]["executionId"] == exec_id
        assert data["task"]["sessionId"] == session_id
        assert data["task"]["taskPayload"]["agent_id"] == agent_id
        assert data["task"]["taskPayload"]["message"]["parts"][0]["text"] == "do work"

        # Session should now be 'working'
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_delivers_prompt(test_database):
    """Waiting worker gets prompt_delivery when task arrives in session inbox."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # First sync: assign the session (consumes the initial task)
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Push a new task to the session inbox (A2A format with baked header)
        with db_conn(ctx["db_url"]) as conn:
            payload = json.dumps(
                {
                    "message": {
                        "role": "user",
                        "parts": [
                            {
                                "kind": "text",
                                "text": "[turn complete from test-agent \u00b7 session fake-child]\n\nchild done",
                            }
                        ],
                    }
                }
            )
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()

        # Worker polls with waiting_for_event
        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "waiting_for_event",
                }
            },
        )

        assert data["type"] == "prompt_delivery"
        assert data["sessionId"] == session_id
        assert isinstance(data["task"]["taskPayload"], dict)
        assert (
            "child done" in data["task"]["taskPayload"]["message"]["parts"][0]["text"]
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_long_poll_wakes(test_database):
    """Long-poll returns prompt_delivery when task is pushed via scheduler API."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Assign session (consumes initial task)
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Set session to input-required so we can push via POST /api/sessions/{id}/message
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

        # Give time for the long-poll to enter select!
        time.sleep(1.0)

        # Push task via scheduler API — this triggers notify_waiters()
        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "wake up"},
            timeout=5,
        )
        assert resp.status_code == 200

        t.join(timeout=10)
        assert not t.is_alive(), "Worker thread should have returned"

        data = result_holder[0]
        assert data["type"] == "prompt_delivery"
        assert isinstance(data["task"]["taskPayload"], dict)
        assert data["task"]["taskPayload"]["message"]["parts"][0]["text"] == "wake up"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_session_complete(test_database):
    """Waiting worker gets session_complete when session reaches terminal state."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Assign session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Mark session as completed directly in DB
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        # Worker polls with waiting_for_event
        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "waiting_for_event",
                }
            },
        )

        assert data["type"] == "session_complete"
        assert data["sessionId"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_persists_agent_session_id(test_database):
    """session_result with agent_session_id updates the DB."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Assign session first
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Report agent_session_id via session_result
        data = _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "agentSessionId": "claude-native-abc123",
                }
            },
        )

        # Verify DB
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT agent_session_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "claude-native-abc123"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_idle_no_sessions(test_database):
    """Idle worker sync returns no_action when no sessions available."""
    with scheduler_context(db_url=test_database) as ctx:
        # No executions created — just sync
        data = _worker_sync(ctx["url"])
        assert data["type"] == "no_action"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_running_heartbeat(test_database):
    """Worker reporting 'running' status gets no_action (heartbeat ack)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Assign session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Report running — should get heartbeat ack
        data = _worker_sync(
            ctx["url"],
            {
                "sessionState": {
                    "sessionId": session_id,
                    "status": "running",
                }
            },
        )
        assert data["type"] == "no_action"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_session_result_then_idle_assigns_next(test_database):
    """session_result + no session_state = process result then assign next session."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        # Create two executions so there are two sessions
        _, session_id_1 = create_execution_via_api(ctx["url"], agent_id, "task 1")
        _, session_id_2 = create_execution_via_api(ctx["url"], agent_id, "task 2")
        both = {session_id_1, session_id_2}

        # Assign first session (order is deterministic by created_at then id,
        # but UUIDs are random so we just track which one we got)
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        first_sid = data["sessionId"]
        assert first_sid in both
        second_sid = (both - {first_sid}).pop()

        # Report result for first session + idle (no session_state)
        # Should persist agent_session_id AND assign second session
        data = _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": first_sid,
                    "agentSessionId": "native-1",
                }
            },
        )
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == second_sid

        # Verify first session's agent_session_id was persisted
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT agent_session_id FROM sessions WHERE id = ?", (first_sid,)
            ).fetchone()
        assert row[0] == "native-1"
