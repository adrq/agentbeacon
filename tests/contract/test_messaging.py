# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

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
from tests.mock_agent_helpers import (
    get_task_queue_count,
    rest_escalate_ok,
    set_session_fields,
)


def _delegate(ctx, lead_session_id, child_agent_name, prompt="do work"):
    result = mcp_tools_call(
        ctx["url"],
        lead_session_id,
        "delegate",
        {"agent": child_agent_name, "prompt": prompt},
    )
    return json.loads(result["content"][0]["text"])["session_id"]


def _send_lateral(ctx, sender_session_id, to, body):
    return httpx.post(
        f"{ctx['url']}/api/v1/messages",
        json={"to": to, "parts": [{"text": body}]},
        headers={"Authorization": f"Bearer {sender_session_id}"},
        timeout=5,
    )


def _get_messages(ctx, session_id, since_id=None):
    params = {"session_id": session_id}
    if since_id is not None:
        params["since_id"] = since_id
    return httpx.get(f"{ctx['url']}/api/v1/messages", params=params, timeout=5)


def _get_discovery(ctx, execution_id):
    return httpx.get(
        f"{ctx['url']}/api/v1/executions/{execution_id}/sessions", timeout=5
    )


def _get_child_hier_name(ctx, exec_id, child_session_id):
    disc = _get_discovery(ctx, exec_id)
    assert disc.status_code == 200
    for entry in disc.json():
        if entry["session_id"] == child_session_id:
            return entry["hierarchical_name"]
    raise AssertionError(f"session {child_session_id} not in discovery")


def _event_type_counts(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE session_id = ?", (session_id,)
        ).fetchall()
    counts = {"message": 0, "message_delivered": 0, "marker": 0}
    for event_type, payload in rows:
        if event_type == "message":
            counts["message"] += 1
        elif event_type == "platform":
            obj = json.loads(payload)
            if obj.get("type") == "message_delivered":
                counts["message_delivered"] += 1
            parts = obj.get("parts") or []
            if parts and parts[0].get("data", {}).get("type") == "question_answer":
                counts["marker"] += 1
    return counts


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_still_works(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "JWT"}]},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "event_id" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_to_running_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        count_before = get_task_queue_count(ctx["db_url"], session_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "follow up"}]},
            timeout=5,
        )
        assert resp.status_code == 200
        count_after = get_task_queue_count(ctx["db_url"], session_id)
        assert count_after == count_before + 1, (
            f"expected queue to grow by 1: before={count_before}, after={count_after}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_message_to_terminal_session_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        for outcome in ("completed", "failed", "canceled"):
            set_session_fields(
                ctx["db_url"],
                session_id,
                desired="terminate",
                outcome=outcome,
            )
            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "hello"}]},
                timeout=5,
            )
            assert resp.status_code == 409, f"expected 409 for outcome={outcome}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_direct_ui_message_emits_exactly_one_message_delivered(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        base = _event_type_counts(ctx["db_url"], session_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "hi from the user"}]},
            timeout=10,
        )
        assert resp.status_code == 200, resp.text

        after = _event_type_counts(ctx["db_url"], session_id)
        assert after["message"] - base["message"] == 1
        assert after["message_delivered"] - base["message_delivered"] == 1
        assert after["marker"] - base["marker"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_lateral_message_no_message_delivered_and_non_resolving(test_database):
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
        set_session_fields(
            ctx["db_url"],
            child_session_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        base = _event_type_counts(ctx["db_url"], child_session_id)
        resp = httpx.post(
            f"{ctx['url']}/api/v1/messages",
            json={
                "to": child_name,
                "parts": [
                    {"text": "revision needed"},
                    {
                        "data": {
                            "type": "question_answer",
                            "batch_id": "not-a-real-batch",
                        }
                    },
                ],
            },
            headers={"Authorization": f"Bearer {lead_session_id}"},
            timeout=10,
        )
        assert resp.status_code == 200, resp.text

        after = _event_type_counts(ctx["db_url"], child_session_id)
        assert after["message"] - base["message"] == 1
        assert after["message_delivered"] - base["message_delivered"] == 0
        assert after["marker"] - base["marker"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_wakes_session(test_database):
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

        set_session_fields(
            ctx["db_url"],
            child_session_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        count_before = get_task_queue_count(ctx["db_url"], child_session_id)

        resp = _send_lateral(ctx, lead_session_id, child_name, "revision needed")
        assert resp.status_code == 200
        assert resp.json()["recipient_session_id"] == child_session_id

        count_after = get_task_queue_count(ctx["db_url"], child_session_id)
        assert count_after == count_before + 1, (
            f"expected queue to grow by 1: before={count_before}, after={count_after}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_running_session(test_database):
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

        set_session_fields(
            ctx["db_url"],
            child_session_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        count_before = get_task_queue_count(ctx["db_url"], child_session_id)

        resp = _send_lateral(ctx, lead_session_id, child_name, "extra info")
        assert resp.status_code == 200

        count_after = get_task_queue_count(ctx["db_url"], child_session_id)
        assert count_after == count_before + 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_terminal_session_rejected(test_database):
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

        set_session_fields(
            ctx["db_url"], child_session_id, desired="terminate", outcome="completed"
        )

        resp = _send_lateral(ctx, lead_session_id, child_name, "too late")
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_unknown_recipient(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = _send_lateral(ctx, session_id, "nonexistent/path", "hello")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_cross_execution_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")

        _, session_a = create_execution_via_api(ctx["url"], agent_id, "task A")
        exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "task B")

        disc = _get_discovery(ctx, exec_b)
        b_name = disc.json()[0]["hierarchical_name"]

        resp = _send_lateral(ctx, session_a, b_name, "cross exec")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_no_auth_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/messages",
            json={"to": "some-name", "parts": [{"text": "hello"}]},
            timeout=5,
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_records_event(test_database):
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
        set_session_fields(
            ctx["db_url"],
            child_session_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        _send_lateral(ctx, lead_session_id, child_name, "review this")

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
                (child_session_id,),
            ).fetchall()

        msg_events = [(et, json.loads(p)) for et, p in events if et == "message"]
        assert len(msg_events) >= 1
        payload = msg_events[-1][1]
        assert payload["role"] == "ROLE_USER"

        data_parts = [
            p
            for p in payload["parts"]
            if "data" in p and p.get("data", {}).get("type") == "sender"
        ]
        assert len(data_parts) == 1
        assert data_parts[0]["data"]["session_id"] == lead_session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_delivery_payload_format(test_database):
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
        set_session_fields(
            ctx["db_url"], child_session_id, desired="run", executor_state="running"
        )

        _send_lateral(ctx, lead_session_id, child_name, "check this")

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchall()

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

        resp = _send_lateral(ctx, lead_session_id, child_name, "too early")
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_to_self(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        disc = _get_discovery(ctx, exec_id)
        my_name = disc.json()[0]["hierarchical_name"]

        set_session_fields(
            ctx["db_url"], session_id, desired="run", executor_state="running"
        )

        resp = _send_lateral(ctx, session_id, my_name, "note to self")
        assert resp.status_code == 200
        assert resp.json()["recipient_session_id"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_sender_uses_hierarchical_name(test_database):
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
        set_session_fields(
            ctx["db_url"], child_session_id, desired="run", executor_state="running"
        )

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
            if "data" in p and p.get("data", {}).get("type") == "sender"
        ]
        assert len(sender_parts) == 1
        assert sender_parts[0]["data"]["name"] == lead_name
        assert "/" not in lead_name


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_lateral_message_from_terminated_session_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        disc = _get_discovery(ctx, exec_id)
        my_name = disc.json()[0]["hierarchical_name"]

        set_session_fields(
            ctx["db_url"], session_id, desired="terminate", outcome="completed"
        )

        resp = _send_lateral(ctx, session_id, my_name, "from the grave")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_returns_history(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "q?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "answer"}]},
            timeout=5,
        )

        resp = _get_messages(ctx, session_id)
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1
        assert any(m["body"] == "answer" for m in messages)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_since_id_filter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "q?"}], "importance": "blocking"},
        )

        resp1 = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "first"}]},
            timeout=5,
        )
        first_event_id = resp1.json()["event_id"]

        set_session_fields(
            ctx["db_url"], session_id, desired="stop", executor_state="idle"
        )
        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "second"}]},
            timeout=5,
        )

        resp = _get_messages(ctx, session_id, since_id=first_event_id)
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) == 1
        assert messages[0]["body"] == "second"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_includes_sender_info(test_database):
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
        set_session_fields(
            ctx["db_url"], child_session_id, desired="run", executor_state="running"
        )

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
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "q?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "user answer"}]},
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
    with scheduler_context(db_url=test_database) as ctx:
        resp = _get_messages(ctx, "00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_returns_session_entries(test_database):
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
        assert entry["desired"] == "run"
        assert entry["executor_state"] == "unassigned"
        assert entry["parent_name"] is None
        assert "hierarchical_name" in entry
        assert len(entry["hierarchical_name"]) > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_hierarchical_names(test_database):
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

        assert "/" not in lead_name

        assert mid_name.startswith(lead_name + "/")
        assert mid_name.count("/") == 1

        assert leaf_name.startswith(mid_name + "/")
        assert leaf_name.count("/") == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_slugs_unique_among_siblings(test_database):
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

        root_slug = root_name
        child_slug = child_name.split("/")[-1]
        assert root_slug != "worker"
        assert child_slug != "worker"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_includes_terminal_sessions(test_database):
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
        set_session_fields(
            ctx["db_url"], child_session, desired="terminate", outcome="completed"
        )

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        child_entries = [e for e in entries if e["session_id"] == child_session]
        assert len(child_entries) == 1
        assert child_entries[0]["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_discovery_parent_name_populated(test_database):
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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slugs_unique_among_siblings(test_database):
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
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("UPDATE sessions SET slug = '' WHERE id = ?", (session_id,))
            conn.commit()

        resp = _get_discovery(ctx, exec_id)
        entries = resp.json()
        assert len(entries) == 1
        assert entries[0]["hierarchical_name"] == session_id[:8]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_slugs_scoped_per_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")

        exec1_id, session1 = create_execution_via_api(ctx["url"], agent_id, "task1")
        exec2_id, session2 = create_execution_via_api(ctx["url"], agent_id, "task2")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET slug = 'same-slug' WHERE id = ?", (session1,)
            )
            conn.execute(
                "UPDATE sessions SET slug = 'same-slug' WHERE id = ?", (session2,)
            )
            conn.commit()

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

        with db_conn(ctx["db_url"]) as conn:
            child1_slug = conn.execute(
                "SELECT slug FROM sessions WHERE id = ?", (child1,)
            ).fetchone()[0]

        set_session_fields(
            ctx["db_url"], child1, desired="terminate", outcome="completed"
        )

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

        children = []
        for i in range(5):
            child = _delegate(ctx, lead_session, "worker", prompt=f"t{i}")
            children.append(child)

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
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="agent")

        exec_ids = []
        for i in range(10):
            exec_id, _ = create_execution_via_api(ctx["url"], agent_id, f"task{i}")
            exec_ids.append(exec_id)

        assert len(exec_ids) == 10
