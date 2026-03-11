"""Contract tests for messaging and agent discovery (Task I).

Tests cover:
- Shared service / post_message refactor (user messaging)
- Lateral messaging (POST /api/messages)
- Message history (GET /api/messages)
- Session discovery endpoint (GET /api/executions/{id}/sessions)
- deliver_to_parent state transition
- Slug generation
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


def _set_session_status(db_url, session_id, status):
    """Directly set session status in DB for test setup."""
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
        conn.commit()


def _delegate(ctx, lead_session_id, child_agent_name, prompt="do work"):
    """Delegate and return child_session_id."""
    result = mcp_tools_call(
        ctx["url"],
        lead_session_id,
        "delegate",
        {"agent": child_agent_name, "prompt": prompt},
    )
    return json.loads(result["content"][0]["text"])["session_id"]


def _send_lateral(ctx, sender_session_id, to, body):
    """POST /api/messages with Bearer auth."""
    return httpx.post(
        f"{ctx['url']}/api/messages",
        json={"to": to, "body": body},
        headers={"Authorization": f"Bearer {sender_session_id}"},
        timeout=5,
    )


def _get_messages(ctx, session_id, since_id=None):
    """GET /api/messages?session_id=...&since_id=..."""
    params = {"session_id": session_id}
    if since_id is not None:
        params["since_id"] = since_id
    return httpx.get(f"{ctx['url']}/api/messages", params=params, timeout=5)


def _get_discovery(ctx, execution_id):
    """GET /api/executions/{id}/sessions (session discovery endpoint)."""
    return httpx.get(f"{ctx['url']}/api/executions/{execution_id}/sessions", timeout=5)


def _worker_sync(url, payload=None, timeout=10):
    """POST /api/worker/sync with optional JSON body."""
    if payload is None:
        payload = {}
    resp = httpx.post(f"{url}/api/worker/sync", json=payload, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _get_child_hier_name(ctx, exec_id, child_session_id):
    """Get hierarchical name for a child session from session discovery."""
    disc = _get_discovery(ctx, exec_id)
    assert disc.status_code == 200
    for entry in disc.json():
        if entry["session_id"] == child_session_id:
            return entry["hierarchical_name"]
    raise AssertionError(f"session {child_session_id} not in discovery")


# --- Shared service / post_message refactor ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_still_works(test_database):
    """Existing user→agent messaging works after refactor."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "JWT"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "event_id" in data
        assert data["session_status"] == "working"
        assert data["execution_status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_to_working_session(test_database):
    """User can queue message to working session (no state transition)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        # Session starts as submitted; set to working directly
        _set_session_status(ctx["db_url"], session_id, "working")

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "follow up"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["session_status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_to_terminal_session_rejected(test_database):
    """Message to completed/failed/canceled session returns 409."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        for status in ("completed", "failed", "canceled"):
            _set_session_status(ctx["db_url"], session_id, status)
            resp = httpx.post(
                f"{ctx['url']}/api/sessions/{session_id}/message",
                json={"message": "hello"},
                timeout=5,
            )
            assert resp.status_code == 409, f"expected 409 for status={status}"


# --- Lateral messaging (POST /api/messages) ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_wakes_session(test_database):
    """Agent message to input-required session transitions to working."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)

        _set_session_status(ctx["db_url"], child_session_id, "input-required")

        resp = _send_lateral(ctx, lead_session_id, child_name, "revision needed")
        assert resp.status_code == 200
        assert resp.json()["recipient_session_id"] == child_session_id
        assert resp.json()["session_status"] == "working"

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?",
                (child_session_id,),
            ).fetchone()
        assert row[0] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_working_session(test_database):
    """Agent message to working session queued (no transition)."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)

        _set_session_status(ctx["db_url"], child_session_id, "working")

        resp = _send_lateral(ctx, lead_session_id, child_name, "extra info")
        assert resp.status_code == 200
        assert resp.json()["session_status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_terminal_session_rejected(test_database):
    """Agent message to completed session returns 409."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)

        _set_session_status(ctx["db_url"], child_session_id, "completed")

        resp = _send_lateral(ctx, lead_session_id, child_name, "too late")
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_unknown_recipient(test_database):
    """Message to non-existent agent name returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = _send_lateral(ctx, session_id, "nonexistent/path", "hello")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_cross_execution_rejected(test_database):
    """Agent in execution A cannot message agent in execution B."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")

        _, session_a = create_execution_via_api(ctx["url"], agent_id, "task A")
        exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "task B")

        # Get name of session_b's root from its own execution
        disc = _get_discovery(ctx, exec_b)
        b_name = disc.json()[0]["hierarchical_name"]

        # Try to message session_b from execution A — should fail (name not in exec A)
        resp = _send_lateral(ctx, session_a, b_name, "cross exec")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_no_auth_rejected(test_database):
    """POST /api/messages without Bearer token returns 401."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/messages",
            json={"to": "some-name", "body": "hello"},
            timeout=5,
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_records_event(test_database):
    """Lateral message creates event with sender data part on recipient session."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)
        _set_session_status(ctx["db_url"], child_session_id, "input-required")

        _send_lateral(ctx, lead_session_id, child_name, "review this")

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
                (child_session_id,),
            ).fetchall()

        msg_events = [(et, json.loads(p)) for et, p in events if et == "message"]
        assert len(msg_events) >= 1
        payload = msg_events[-1][1]
        assert payload["role"] == "user"

        # Verify sender data part
        data_parts = [
            p
            for p in payload["parts"]
            if p.get("kind") == "data" and p.get("data", {}).get("type") == "sender"
        ]
        assert len(data_parts) == 1
        assert data_parts[0]["data"]["session_id"] == lead_session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_delivery_payload_format(test_database):
    """Verify task_queue entry has correct A2A envelope with sender header."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)
        _set_session_status(ctx["db_url"], child_session_id, "working")

        _send_lateral(ctx, lead_session_id, child_name, "check this")

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchall()

        # Find the lateral message payload (skip bootstrap task)
        payloads = [json.loads(r[0]) for r in rows]
        msg_payloads = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(msg_payloads) >= 1
        delivery = msg_payloads[-1]
        text = delivery["message"]["parts"][0]["text"]
        assert "[message from" in text
        assert lead_session_id in text
        assert "check this" in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_submitted_session_rejected(test_database):
    """Message to submitted session (not yet started) returns 409."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)

        # Child is in "submitted" state by default after delegate
        resp = _send_lateral(ctx, lead_session_id, child_name, "too early")
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_self(test_database):
    """Agent can send message to itself (no restriction)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        disc = _get_discovery(ctx, exec_id)
        my_name = disc.json()[0]["hierarchical_name"]

        _set_session_status(ctx["db_url"], session_id, "working")

        resp = _send_lateral(ctx, session_id, my_name, "note to self")
        assert resp.status_code == 200
        assert resp.json()["recipient_session_id"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_sender_uses_hierarchical_name(test_database):
    """Sender info in event uses hierarchical name, not just agent config name."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)
        _set_session_status(ctx["db_url"], child_session_id, "working")

        # Get lead's hierarchical name from session discovery
        disc = _get_discovery(ctx, exec_id)
        lead_name = None
        for entry in disc.json():
            if entry["session_id"] == lead_session_id:
                lead_name = entry["hierarchical_name"]
                break
        assert lead_name is not None

        _send_lateral(ctx, lead_session_id, child_name, "hi child")

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY id DESC",
                (child_session_id,),
            ).fetchall()

        payload = json.loads(events[0][0])
        sender_parts = [
            p
            for p in payload["parts"]
            if p.get("kind") == "data" and p.get("data", {}).get("type") == "sender"
        ]
        assert len(sender_parts) == 1
        # Sender name is the hierarchical slug path, not agent config name "lead"
        assert sender_parts[0]["data"]["name"] == lead_name
        assert "/" not in lead_name  # root has no /


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_from_terminated_session_rejected(test_database):
    """Completed session's Bearer token returns 404 (MCP spec: terminated → 404)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        disc = _get_discovery(ctx, exec_id)
        my_name = disc.json()[0]["hierarchical_name"]

        _set_session_status(ctx["db_url"], session_id, "completed")

        resp = _send_lateral(ctx, session_id, my_name, "from the grave")
        assert resp.status_code == 404


# --- Message history (GET /api/messages) ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_returns_history(test_database):
    """GET /api/messages?session_id= returns message events."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "q?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "answer"},
            timeout=5,
        )

        resp = _get_messages(ctx, session_id)
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1
        assert any(m["body"] == "answer" for m in messages)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_since_id_filter(test_database):
    """since_id= parameter filters to messages after event ID."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "q?"}], "importance": "blocking"},
        )

        # Send first message
        resp1 = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "first"},
            timeout=5,
        )
        first_event_id = resp1.json()["event_id"]

        # Transition back to input-required and send second
        _set_session_status(ctx["db_url"], session_id, "input-required")
        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "second"},
            timeout=5,
        )

        # Get messages since first event
        resp = _get_messages(ctx, session_id, since_id=first_event_id)
        assert resp.status_code == 200
        messages = resp.json()
        # Should only have the second message
        assert len(messages) == 1
        assert messages[0]["body"] == "second"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_includes_sender_info(test_database):
    """Agent messages include sender name and session_id."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        child_session_id = _delegate(ctx, lead_session_id, "child")
        child_name = _get_child_hier_name(ctx, exec_id, child_session_id)
        _set_session_status(ctx["db_url"], child_session_id, "working")

        _send_lateral(ctx, lead_session_id, child_name, "from lead")

        resp = _get_messages(ctx, child_session_id)
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1
        msg = messages[-1]
        assert msg["sender"] is not None
        assert msg["sender"]["session_id"] == lead_session_id
        assert msg["sender"]["name"] != ""


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_user_messages_no_sender(test_database):
    """User messages have null sender field."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "q?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "user answer"},
            timeout=5,
        )

        resp = _get_messages(ctx, session_id)
        assert resp.status_code == 200
        messages = resp.json()
        user_msgs = [m for m in messages if m["body"] == "user answer"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["sender"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_nonexistent_session(test_database):
    """GET /api/messages for unknown session_id returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = _get_messages(ctx, "00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# --- Discovery (GET /api/executions/{id}/sessions) ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_returns_session_entries(test_database):
    """Session discovery returns session-level entries."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="disc-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = _get_discovery(ctx, exec_id)
        assert resp.status_code == 200
        entries = resp.json()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["session_id"] == session_id
        assert entry["agent_name"] == "disc-agent"
        assert entry["status"] == "submitted"
        assert entry["parent_name"] is None
        assert "hierarchical_name" in entry
        assert len(entry["hierarchical_name"]) > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_hierarchical_names(test_database):
    """Hierarchical names are slug-based paths: root-slug/child-slug/grandchild-slug."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        mid_agent_id = seed_test_agent(ctx["db_url"], name="mid")
        leaf_agent_id = seed_test_agent(ctx["db_url"], name="leaf")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, mid_agent_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, leaf_agent_id),
            )
            conn.commit()
        mid_session = _delegate(ctx, lead_session, "mid")
        leaf_session = _delegate(ctx, mid_session, "leaf")

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        name_map = {e["session_id"]: e["hierarchical_name"] for e in entries}

        lead_name = name_map[lead_session]
        mid_name = name_map[mid_session]
        leaf_name = name_map[leaf_session]

        # Root has no /
        assert "/" not in lead_name

        # Mid path is root/mid-slug
        assert mid_name.startswith(lead_name + "/")
        assert mid_name.count("/") == 1

        # Leaf path is root/mid-slug/leaf-slug
        assert leaf_name.startswith(mid_name + "/")
        assert leaf_name.count("/") == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_slugs_unique_among_siblings(test_database):
    """Two siblings of the same parent get distinct auto-generated slugs."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        worker_agent_id = seed_test_agent(ctx["db_url"], name="worker")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, worker_agent_id),
            )
            conn.commit()
        child1 = _delegate(ctx, lead_session, "worker", prompt="task 1")
        child2 = _delegate(ctx, lead_session, "worker", prompt="task 2")

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        name_map = {e["session_id"]: e["hierarchical_name"] for e in entries}

        child1_name = name_map[child1]
        child2_name = name_map[child2]
        assert child1_name != child2_name


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_same_agent_different_slugs(test_database):
    """Same agent config at multiple levels gets distinct slugs."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="worker")

        exec_id, root_session = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, agent_id),
            )
            conn.commit()
        child_session = _delegate(ctx, root_session, "worker")

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        name_map = {e["session_id"]: e["hierarchical_name"] for e in entries}

        root_name = name_map[root_session]
        child_name = name_map[child_session]

        # Names are distinct slug paths, not "worker/worker"
        root_slug = root_name
        child_slug = child_name.split("/")[-1]
        assert root_slug != "worker"
        assert child_slug != "worker"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_includes_terminal_sessions(test_database):
    """Terminal sessions appear in discovery (with terminal status)."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()
        child_session = _delegate(ctx, lead_session, "child")
        _set_session_status(ctx["db_url"], child_session, "completed")

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        child_entries = [e for e in entries if e["session_id"] == child_session]
        assert len(child_entries) == 1
        assert child_entries[0]["status"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_parent_name_populated(test_database):
    """Each entry's parent_name matches parent's hierarchical name."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()
        child_session = _delegate(ctx, lead_session, "child")

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        name_map = {e["session_id"]: e for e in entries}

        lead_entry = name_map[lead_session]
        child_entry = name_map[child_session]

        assert lead_entry["parent_name"] is None
        assert child_entry["parent_name"] == lead_entry["hierarchical_name"]


# --- deliver_to_parent state transition ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_wakes_parent(test_database):
    """Turn-complete notification transitions parent from input-required to working."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
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

        # Delegate to child
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        # Claim child session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == child_id

        # Lead transitions to input-required (waiting for child)
        _set_session_status(ctx["db_url"], lead_id, "input-required")

        # Child completes turn → should wake parent
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
                                "parts": [{"kind": "text", "text": "done"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (lead_id,)
            ).fetchone()
        assert row[0] == "working"


# --- Slug generation ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slugs_unique_among_siblings(test_database):
    """Two children of same parent get distinct slugs (DB-level check)."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        worker_agent_id = seed_test_agent(ctx["db_url"], name="worker")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, worker_agent_id),
            )
            conn.commit()
        child1 = _delegate(ctx, lead_session, "worker", prompt="t1")
        child2 = _delegate(ctx, lead_session, "worker", prompt="t2")

        with db_conn(ctx["db_url"]) as conn:
            slug1 = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (child1,)
            ).fetchone()[0]
            slug2 = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (child2,)
            ).fetchone()[0]

        assert slug1 != ""
        assert slug2 != ""
        assert slug1 != slug2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_pre_migration_sessions_fallback(test_database):
    """Sessions with empty slugs (pre-migration) use truncated session ID fallback."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        # Simulate pre-migration session by clearing slug
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("UPDATE sessions SET slug = '' WHERE id = ?", (session_id,))
            conn.commit()

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        assert len(entries) == 1
        # Fallback: truncated session ID (first 8 chars)
        assert entries[0]["hierarchical_name"] == session_id[:8]


# --- Slug uniqueness scoping ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_slugs_scoped_per_execution(test_database):
    """Root sessions in different executions can have the same slug without collision."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")

        # Create two separate executions (each gets a root session)
        exec1_id, session1 = create_execution_via_api(ctx["url"], agent_id, "task1")
        exec2_id, session2 = create_execution_via_api(ctx["url"], agent_id, "task2")

        # Force both root sessions to have the same slug
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET slug = 'same-slug' WHERE id = ?", (session1,)
            )
            conn.execute(
                "UPDATE sessions SET slug = 'same-slug' WHERE id = ?", (session2,)
            )
            conn.commit()

        # Both should coexist — verify by reading them back
        with db_conn(ctx["db_url"]) as conn:
            slug1 = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (session1,)
            ).fetchone()[0]
            slug2 = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (session2,)
            ).fetchone()[0]

        assert slug1 == "same-slug"
        assert slug2 == "same-slug"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sibling_slug_includes_terminal(test_database):
    """Completed sibling's slug is still reserved — new sibling cannot reuse it."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        worker_agent_id = seed_test_agent(ctx["db_url"], name="worker")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, worker_agent_id),
            )
            conn.commit()
        child1 = _delegate(ctx, lead_session, "worker", prompt="t1")

        # Get child1's slug
        with db_conn(ctx["db_url"]) as conn:
            child1_slug = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (child1,)
            ).fetchone()[0]

        # Mark child1 as completed (terminal)
        _set_session_status(ctx["db_url"], child1, "completed")

        # Create another child — should get a different slug
        child2 = _delegate(ctx, lead_session, "worker", prompt="t2")

        with db_conn(ctx["db_url"]) as conn:
            child2_slug = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (child2,)
            ).fetchone()[0]

        assert child1_slug != child2_slug, (
            f"New child reused terminal sibling's slug: {child1_slug}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_collision_retry_on_delegate(test_database):
    """Slug collision from DB constraint triggers retry, not 500."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead")
        worker_agent_id = seed_test_agent(ctx["db_url"], name="worker")

        exec_id, lead_session = create_execution_via_api(ctx["url"], lead_id, "task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, worker_agent_id),
            )
            conn.commit()

        # Create many children to increase slug collision probability
        # (not a probabilistic test — we just verify the endpoint doesn't 500)
        children = []
        for i in range(5):
            child = _delegate(ctx, lead_session, "worker", prompt=f"t{i}")
            children.append(child)

        # All 5 should have distinct slugs
        with db_conn(ctx["db_url"]) as conn:
            slugs = []
            for cid in children:
                slug = conn.execute(
                    "SELECT slug FROM sessions WHERE id = ?", (cid,)
                ).fetchone()[0]
                slugs.append(slug)

        assert len(set(slugs)) == 5, f"Expected 5 unique slugs, got: {slugs}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_slug_not_empty(test_database):
    """Root session gets a non-empty slug at creation."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with db_conn(ctx["db_url"]) as conn:
            slug = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]

        assert slug != "", "Root session should have a non-empty slug"
        assert "-" in slug, f"Slug should be adjective-noun format: {slug}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_multiple_executions_root_slugs_independent(test_database):
    """Creating many executions succeeds even if slug pool is small relative to count."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")

        # Create 10 executions — each root session is in its own execution
        # so all should succeed even with identical slugs
        exec_ids = []
        for i in range(10):
            exec_id, _ = create_execution_via_api(ctx["url"], agent_id, f"task{i}")
            exec_ids.append(exec_id)

        assert len(exec_ids) == 10
