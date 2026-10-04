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
    create_child_session_raw,
    make_executor_report,
    make_turn_result,
    rest_escalate_ok,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_bootstrap_has_driver_object(test_database):
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
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "JWT"}]},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        message_payloads = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(message_payloads) >= 1, f"No user message payload found: {payloads}"
        msg = message_payloads[-1]["message"]
        assert "text" in msg["parts"][0]
        assert msg["parts"][0]["text"] == "JWT"
        assert not msg["parts"][0]["text"].startswith("[user]")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_has_driver_object(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, agent_id),
            )
            conn.commit()

        data = post_worker_sync(ctx["url"], worker_id="w-delegate")
        assert data["type"] == "command"

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "lead-agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

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
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, agent_id),
            )
            conn.commit()

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_id, "running"),
            command_ack=token,
        )

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "test-agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        data = post_worker_sync(ctx["url"], worker_id="w2")
        assert data["type"] == "command"
        child_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child_id, "running"),
            command_ack=child_token,
        )

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "auth done"}]},
                ],
            ),
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        delivery = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(delivery) >= 1, f"No turn-complete notification found: {payloads}"
        parts = delivery[-1]["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1, "notification should have a data part"
        data = data_parts[0]["data"]
        assert data["type"] == "turn_complete"
        assert data["child_session_id"] == child_id
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        for t in text_parts:
            assert "auth done" not in t, (
                "notification should not embed raw child output"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_terminated_notification_is_a2a(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        notifications = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(notifications) >= 1, f"No termination notification found: {payloads}"
        parts = notifications[-1]["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1, "termination notification should have a data part"
        data = data_parts[0]["data"]
        assert data["child_session_id"] == child_id
