"""Contract tests for turn-complete auto-notification (Task TC).

Tests verify that when a child agent's turn completes, the scheduler
delivers the child's output to the parent session's inbox and records
a platform event on the parent session.
"""

import json
import threading
import time

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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_delivers_to_parent(test_database):
    """Child turn (A2A format) delivers formatted message to parent's task_queue."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        # Child completes turn with A2A format message
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
                                "parts": [{"kind": "text", "text": "auth done"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify task_queue entry for parent
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchone()

        assert row is not None, "task_queue should have delivery for parent"
        payload = json.loads(row[0])
        assert isinstance(payload, dict)
        text = payload["message"]["parts"][0]["text"]
        assert "[turn complete from" in text
        assert child_id in text
        assert "auth done" in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_records_parent_event(test_database):
    """Platform event (type: turn_complete) recorded on parent session."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

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
                                "parts": [{"kind": "text", "text": "result text"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Check platform events on parent session
        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
                (lead_id,),
            ).fetchall()

        platform_events = [(et, json.loads(p)) for et, p in events if et == "platform"]

        turn_complete_events = []
        for _, payload in platform_events:
            for part in payload.get("parts", []):
                if part.get("kind") == "data":
                    data = part["data"]
                    if data.get("type") == "turn_complete":
                        turn_complete_events.append(data)

        assert len(turn_complete_events) == 1
        tc = turn_complete_events[0]
        assert tc["child_session_id"] == child_id
        assert tc["message"] == "result text"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_root_lead_no_delivery(test_database):
    """Root lead turn completes without delivery (no parent)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")

        # Claim lead session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Lead completes turn
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
                                "parts": [{"kind": "text", "text": "lead output"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify: no task_queue entry (root lead has no parent to deliver to)
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchone()
        assert row[0] == 0, "root lead should not have task_queue delivery"

        # Verify execution went to input-required
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()
        assert row[0] == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_empty_turn_no_delivery(test_database):
    """Child turn with empty turnMessages does not deliver to parent."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        # Child completes turn with NO messages
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": child_id,
                    "turnMessages": [],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify: no task_queue entry for parent (nothing to deliver)
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchone()
        assert row[0] == 0, "empty turn should not produce delivery"

        # Child should still be input-required
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()
        assert row[0] == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_parent_long_poll_wakes(test_database):
    """Parent in long-poll wakes when child turn-complete is delivered."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        # Start parent long-poll in background thread
        result_holder = [None]

        def parent_wait():
            result_holder[0] = _worker_sync(
                ctx["url"],
                {
                    "sessionState": {
                        "sessionId": lead_id,
                        "status": "waiting_for_event",
                    }
                },
                timeout=40,
            )

        t = threading.Thread(target=parent_wait)
        t.start()

        # Give time for long-poll to enter select!
        time.sleep(1.0)

        # Child completes turn — this should wake the parent
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
                                "parts": [{"kind": "text", "text": "woke parent"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        t.join(timeout=10)
        assert not t.is_alive(), "Parent long-poll should have returned"

        data = result_holder[0]
        assert data["type"] == "prompt_delivery"
        assert data["sessionId"] == lead_id
        assert isinstance(data["task"]["taskPayload"], dict)
        text = data["task"]["taskPayload"]["message"]["parts"][0]["text"]
        assert "woke parent" in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_claude_sdk_format(test_database):
    """Child turn with Claude SDK {content: [{type: "text", text: "..."}]} extracts text."""
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        # Child completes turn with Claude SDK format
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
                                "content": [
                                    {"type": "text", "text": "claude sdk output"}
                                ],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        # Verify delivery to parent
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchone()

        assert row is not None, "Claude SDK format should still deliver"
        payload = json.loads(row[0])
        text = payload["message"]["parts"][0]["text"]
        assert "claude sdk output" in text
