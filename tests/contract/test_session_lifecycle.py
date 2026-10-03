# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    insert_task,
    make_executor_report,
    make_turn_result,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_enqueues_task(test_database):
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
def test_worker_sync_assigns_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

        data = post_worker_sync(ctx["url"], worker_id="w1")

        assert data["type"] == "command"
        assert "token" in data
        action = data["action"]
        assert action["type"] == "assign"
        assert action["session_id"] == session_id
        assert action["payload"] is not None
        assert action["payload"]["agent_id"] == agent_id
        assert action["payload"]["message"]["parts"][0]["text"] == "do work"
        assert action["resume"] is False

        assert_session_state(
            ctx["db_url"], session_id, desired="run", executor_state="unassigned"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_delivers_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assign_token = data["token"]

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "idle"),
        )

        insert_task(ctx["db_url"], exec_id, session_id, "child done")

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "idle"),
        )

        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "feed_turn"
        assert action["session_id"] == session_id
        assert "child done" in action["payload"]["message"]["parts"][0]["text"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_message_delivers_feed_turn_on_next_sync(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )
        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "idle"),
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "wake up"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "feed_turn"
        assert action["session_id"] == session_id
        assert action["payload"]["message"]["parts"][0]["text"] == "wake up"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_session_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )
        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "idle"),
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'completed' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "idle"),
        )

        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "cancel"
        assert action["session_id"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_persists_agent_session_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="claude-native-abc123"
            ),
            command_ack=assign_token,
        )

        assert_session_state(
            ctx["db_url"],
            session_id,
            agent_session_id="claude-native-abc123",
            executor_state="running",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_idle_no_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "no_action"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_running_heartbeat(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(session_id, "running"),
            timeout=35,
        )
        assert data["type"] == "no_action"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_turn_result_then_idle_assigns_next(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id_1, session_id_1 = create_execution_via_api(
            ctx["url"], agent_id, "task 1"
        )
        exec_id_2, session_id_2 = create_execution_via_api(
            ctx["url"], agent_id, "task 2"
        )
        both = {session_id_1, session_id_2}

        data = post_worker_sync(ctx["url"], worker_id="w1")
        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        first_sid = data["action"]["session_id"]
        assert first_sid in both
        second_sid = (both - {first_sid}).pop()
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(first_sid, "running", "sdk-1"),
            command_ack=assign_token,
        )

        data = post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(first_sid, "idle"),
            turn_result=make_turn_result(
                first_sid,
                messages=[{"role": "ROLE_AGENT", "parts": [{"text": "task 1 done"}]}],
            ),
        )

        first_exec = exec_id_1 if first_sid == session_id_1 else exec_id_2
        httpx.post(
            f"{ctx['url']}/api/v1/executions/{first_exec}/terminate",
            timeout=10,
        )

        for _ in range(10):
            if data["type"] == "command" and data["action"]["type"] == "assign":
                if data["action"]["session_id"] == second_sid:
                    break
            if data["type"] == "command" and data["action"]["type"] == "cancel":
                data = post_worker_sync(
                    ctx["url"],
                    "w1",
                    executor_report=make_executor_report(first_sid, "idle"),
                    command_ack=data["token"],
                )
            else:
                data = post_worker_sync(ctx["url"], "w1")

        assert data["type"] == "command"
        assert data["action"]["type"] == "assign"
        assert data["action"]["session_id"] == second_sid
