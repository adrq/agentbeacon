"""Contract tests for unified A2A input format (Task IF).

Verifies that all task_payload entries use A2A Message format:
  {message: {role, parts}}
with no plain string payloads remaining. Bootstrap/delegate payloads
use `driver.platform` instead of flat `agent_type`.
"""

import json
import uuid

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
    if payload is None:
        payload = {}
    resp = httpx.post(f"{url}/api/worker/sync", json=payload, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_bootstrap_has_driver_object(test_database):
    """Initial task payload uses driver.platform instead of flat agent_type."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth"
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert "driver" in payload, f"Bootstrap payload missing driver: {payload}"
        assert payload["driver"]["platform"] == "claude_sdk"
        assert "config" in payload["driver"]
        assert "agent_type" not in payload, "Flat agent_type should not exist"
        assert "sandbox_config" not in payload, "Flat sandbox_config should not exist"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_is_a2a(test_database):
    """POST /api/sessions/{id}/message produces A2A payload in task_queue."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        # Escalate to input-required so we can post a user message
        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "JWT"},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        # Find user message (skip bootstrap which has "driver")
        message_payloads = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(message_payloads) >= 1, f"No user message payload found: {payloads}"
        msg = message_payloads[-1]["message"]
        assert msg["parts"][0]["kind"] == "text"
        assert msg["parts"][0]["text"] == "JWT"
        # No [user] prefix
        assert not msg["parts"][0]["text"].startswith("[user]")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_has_driver_object(test_database):
    """Delegate task payload uses driver.platform."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Claim lead session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Delegate to child
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "lead-agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        # Read child's task_queue entry
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert "driver" in payload, f"Delegate payload missing driver: {payload}"
        assert payload["driver"]["platform"] == "claude_sdk"
        assert "config" in payload["driver"]
        assert "agent_type" not in payload
        assert "sandbox_config" not in payload


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_is_a2a(test_database):
    """Turn-complete delivery creates A2A payload with baked header."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")

        # Claim lead
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Delegate to child
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "test-agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        # Claim child
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == child_id

        # Complete child turn
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

        # Read parent's task_queue for the delivery
        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        # Find the turn-complete delivery (skip bootstrap which has "driver")
        delivery = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(delivery) >= 1, f"No turn-complete delivery found: {payloads}"
        text = delivery[-1]["message"]["parts"][0]["text"]
        assert "[turn complete from" in text
        assert "auth done" in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_terminated_notification_is_a2a(test_database):
    """Parent termination notification uses A2A with baked header."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Insert a child session directly (status=working so it can be canceled)
        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
                (child_id, exec_id, lead_id, agent_id, "working"),
            )
            conn.commit()

        # Cancel the child session
        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/cancel", timeout=10)
        assert resp.status_code == 200

        # Read parent's task_queue for the notification
        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        # Find the termination notification (skip bootstrap)
        notifications = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(notifications) >= 1, f"No termination notification found: {payloads}"
        text = notifications[-1]["message"]["parts"][0]["text"]
        assert "[session" in text
        assert "was canceled by user" in text
