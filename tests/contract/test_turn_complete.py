# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    get_task_queue_count,
    get_task_queue_entry,
    make_executor_report,
    make_turn_result,
    post_worker_sync,
)


def _setup_parent_child(ctx, agent_name="test-agent"):
    agent_id = seed_test_agent(ctx["db_url"], name=agent_name)
    exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
            (exec_id, agent_id),
        )
        conn.commit()

    data = post_worker_sync(ctx["url"], worker_id="w1")
    assert data["type"] == "command"
    assert data["action"]["type"] == "assign"
    assert data["action"]["session_id"] == lead_id
    lead_assign_token = data["token"]

    post_worker_sync(
        ctx["url"],
        "w1",
        executor_report=make_executor_report(lead_id, "running"),
        command_ack=lead_assign_token,
    )

    result = mcp_tools_call(
        ctx["url"],
        lead_id,
        "delegate",
        {"agent": agent_name, "prompt": "child task"},
    )
    child_id = json.loads(result["content"][0]["text"])["session_id"]

    data = post_worker_sync(ctx["url"], worker_id="w2")
    assert data["type"] == "command"
    assert data["action"]["type"] == "assign"
    assert data["action"]["session_id"] == child_id
    child_assign_token = data["token"]

    post_worker_sync(
        ctx["url"],
        "w2",
        executor_report=make_executor_report(child_id, "running"),
        command_ack=child_assign_token,
    )

    with db_conn(ctx["db_url"]) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_id,))
        conn.commit()

    return exec_id, lead_id, child_id, agent_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_notifies_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[
                    {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "auth done"}],
                    }
                ],
            ),
        )

        entry = get_task_queue_entry(ctx["db_url"], lead_id)
        assert entry is not None, "task_queue should have notification for parent"
        payload, source = entry

        parts = payload["message"]["parts"]
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

        assert source == f"child_result:{child_id}"

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform'",
                (lead_id,),
            ).fetchall()
        tc_events = []
        for (p_str,) in events:
            p = json.loads(p_str)
            for part in p.get("parts", []):
                if "data" in part and part["data"].get("type") == "turn_complete":
                    tc_events.append(part["data"])
        assert len(tc_events) >= 1, (
            "platform event should be persisted on parent (dual-write)"
        )
        assert tc_events[0]["child_session_id"] == child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_notification_coexists_with_existing_queue_work(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload, source) "
                "VALUES (?, ?, ?, ?)",
                (
                    exec_id,
                    lead_id,
                    '{"message":{"role":"ROLE_USER","parts":[{"text":"existing work"}]}}',
                    "user",
                ),
            )
            conn.commit()

        before_count = get_task_queue_count(ctx["db_url"], lead_id)
        assert before_count >= 1

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[{"role": "ROLE_AGENT", "parts": [{"text": "child result"}]}],
            ),
        )

        after_count = get_task_queue_count(ctx["db_url"], lead_id)
        assert after_count == before_count + 1, (
            f"notification must coexist with existing work: before={before_count}, after={after_count}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_root_lead_emits_session_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_id, "running"),
            command_ack=assign_token,
        )

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_id, "idle"),
            turn_result=make_turn_result(
                lead_id,
                messages=[
                    {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "lead output"}],
                    }
                ],
            ),
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchone()
        assert row[0] == 0, "root lead should not have task_queue delivery"

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform'",
                (lead_id,),
            ).fetchall()

        found = False
        for (payload_str,) in events:
            payload = json.loads(payload_str)
            for part in payload.get("parts", []):
                if "data" in part and part["data"].get("type") == "turn_complete":
                    found = True
                    break
        assert found, (
            "root session should have a platform event with data.type='turn_complete'"
        )

        assert_session_state(
            ctx["db_url"], lead_id, desired="run", executor_state="idle"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_empty_turn_no_delivery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(child_id, messages=[]),
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (lead_id,),
            ).fetchone()
        assert row[0] == 0, "empty turn should not produce delivery"

        assert_session_state(
            ctx["db_url"], child_id, desired="run", executor_state="idle"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_complete_parent_receives_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id, _ = _setup_parent_child(ctx)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[
                    {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "child output"}],
                    }
                ],
            ),
        )

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_id, "idle"),
        )
        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "feed_turn"
        assert action["session_id"] == lead_id
        parts = action["payload"]["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1, "FeedTurn payload should have a data part"
        data = data_parts[0]["data"]
        assert data["type"] == "turn_complete"
        assert data["child_session_id"] == child_id
