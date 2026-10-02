# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    get_task_queue_count,
    insert_task,
    make_executor_report,
    post_worker_sync,
)


def _setup_assigned_session(ctx):
    agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

    data = post_worker_sync(ctx["url"], worker_id="w1")
    assert data["type"] == "command"
    assert data["action"]["type"] == "assign"
    assert data["action"]["session_id"] == session_id
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

    return exec_id, session_id, "w1"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_worker_gets_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, worker_id = _setup_assigned_session(ctx)

        insert_task(ctx["db_url"], exec_id, session_id, "fetch me")

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
        )

        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "feed_turn"
        assert action["session_id"] == session_id
        assert action["payload"]["message"]["parts"][0]["text"] == "fetch me"

        assert get_task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_worker_no_tasks_returns_no_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _, session_id, worker_id = _setup_assigned_session(ctx)

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
            timeout=35,
        )

        assert data["type"] == "no_action"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminated_session_returns_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, worker_id = _setup_assigned_session(ctx)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'completed' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        insert_task(ctx["db_url"], exec_id, session_id, "should not deliver")

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
        )

        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "cancel"
        assert action["session_id"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_running_worker_mid_turn_feed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, worker_id = _setup_assigned_session(ctx)

        post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
        )

        insert_task(ctx["db_url"], exec_id, session_id, "mid-turn message")

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
        )

        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "feed_turn"
        assert action["session_id"] == session_id
        assert action["payload"]["message"]["parts"][0]["text"] == "mid-turn message"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_command_token_ack_clears_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, worker_id = _setup_assigned_session(ctx)

        insert_task(ctx["db_url"], exec_id, session_id, "first task")

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"
        feed_token = data["token"]

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=feed_token,
        )

        insert_task(ctx["db_url"], exec_id, session_id, "second task")

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
        )

        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"
        assert data["action"]["payload"]["message"]["parts"][0]["text"] == "second task"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_full_round_trip_message_to_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, worker_id = _setup_assigned_session(ctx)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "round trip"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        data = post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert data["type"] == "command"
        action = data["action"]
        assert action["type"] == "feed_turn"
        assert action["session_id"] == session_id
        assert action["payload"]["message"]["parts"][0]["text"] == "round trip"

        assert get_task_queue_count(ctx["db_url"], session_id) == 0
