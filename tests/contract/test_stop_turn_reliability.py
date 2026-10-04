# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import pytest

import httpx

from tests.testhelpers import (
    assert_event_exists,
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    post_user_message,
    scheduler_context,
    seed_test_agent,
    stop_session,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    get_task_queue_count,
    insert_task,
    make_executor_report,
    make_turn_result,
    set_session_fields,
    post_worker_sync,
)

LEAD_WORKER = "w-stop-lead"
CHILD_WORKER = "w-stop-child"


def _setup_parent_child(ctx):
    agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
    exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
            (exec_id, agent_id),
        )
        conn.commit()

    resp = post_worker_sync(ctx["url"], LEAD_WORKER)
    assert resp["type"] == "command"
    assert resp["action"]["type"] == "assign"

    assign_token = resp["token"]
    lead_sid = resp["action"]["session_id"]
    assert lead_sid == lead_id

    post_worker_sync(
        ctx["url"],
        LEAD_WORKER,
        executor_report=make_executor_report(lead_id, "running"),
        command_ack=assign_token,
    )

    result = mcp_tools_call(
        ctx["url"],
        lead_id,
        "delegate",
        {"agent": "stop-agent", "prompt": "child task"},
    )
    child_id = json.loads(result["content"][0]["text"])["session_id"]

    resp = post_worker_sync(ctx["url"], CHILD_WORKER)
    assert resp["type"] == "command"
    assert resp["action"]["type"] == "assign"
    child_assign_token = resp["token"]
    assert resp["action"]["session_id"] == child_id

    post_worker_sync(
        ctx["url"],
        CHILD_WORKER,
        executor_report=make_executor_report(child_id, "running"),
        command_ack=child_assign_token,
    )

    with db_conn(ctx["db_url"]) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_id,))
        conn.commit()

    return exec_id, lead_id, child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_drains_queue_and_issues_stop_turn_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = post_worker_sync(ctx["url"], LEAD_WORKER)
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assign_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        insert_task(ctx["db_url"], exec_id, session_id, "queued before stop")

        stop_resp = stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], session_id) == 0

        assert_session_state(ctx["db_url"], session_id, desired="stop")

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=stop_token,
        )

        assert_session_state(
            ctx["db_url"], session_id, desired="stop", executor_state="idle"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_completion_after_parent_stop_resumes_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        insert_task(ctx["db_url"], exec_id, lead_id, "queued before stop")

        stop_resp = stop_session(ctx["url"], lead_id)
        assert stop_resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], lead_id) == 0

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(lead_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(lead_id, "idle"),
            command_ack=stop_token,
        )

        assert_session_state(
            ctx["db_url"], lead_id, desired="stop", executor_state="idle"
        )

        post_worker_sync(
            ctx["url"],
            CHILD_WORKER,
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "child output"}]},
                ],
            ),
        )

        assert_session_state(ctx["db_url"], lead_id, desired="run")
        assert get_task_queue_count(ctx["db_url"], lead_id) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_replayed_stop_ack_does_not_override_new_work(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = post_worker_sync(ctx["url"], LEAD_WORKER)
        assign_token = resp["token"]
        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        stop_resp = stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=stop_token,
        )

        assert_session_state(
            ctx["db_url"], session_id, desired="stop", executor_state="idle"
        )

        message_resp = post_user_message(ctx["url"], session_id, "new work")
        assert message_resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="run")

        replay_resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=stop_token,
        )
        assert replay_resp["type"] == "command"
        assert replay_resp["action"]["type"] == "feed_turn"

        assert_session_state(ctx["db_url"], session_id, desired="run")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_ack_with_final_turn_output_accepted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = post_worker_sync(ctx["url"], LEAD_WORKER)
        assign_token = resp["token"]
        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        stop_resp = stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(
                session_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "partial output"}]},
                ],
            ),
            command_ack=stop_token,
        )

        assert_session_state(
            ctx["db_url"], session_id, desired="stop", executor_state="idle"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_ack_after_midturn_output_accepted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = post_worker_sync(ctx["url"], LEAD_WORKER)
        assign_token = resp["token"]
        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            turn_result=make_turn_result(
                session_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "mid-turn output"}]}
                ],
            ),
        )

        assert_event_exists(ctx["db_url"], session_id, "message", "mid-turn output")

        stop_resp = stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=stop_token,
        )

        assert_session_state(
            ctx["db_url"], session_id, desired="stop", executor_state="idle"
        )

        assert_event_exists(ctx["db_url"], session_id, "message", "mid-turn output")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_preserves_messages_queued_after_stop(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = post_worker_sync(ctx["url"], LEAD_WORKER)
        assign_token = resp["token"]
        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        stop_resp = stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        message_resp = post_user_message(ctx["url"], session_id, "queued after stop")
        assert message_resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], session_id) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_overrides_pending_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = post_worker_sync(ctx["url"], LEAD_WORKER)
        assign_token = resp["token"]
        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )

        insert_task(ctx["db_url"], exec_id, session_id, "pending work")

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"
        _feed_token = resp["token"]

        stop_resp = stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_during_child_turn_issues_stop_turn_on_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        stop_resp = stop_session(ctx["url"], lead_id)
        assert stop_resp.status_code == 200

        assert_session_state(ctx["db_url"], lead_id, desired="stop")

        assert_session_state(ctx["db_url"], child_id, desired="run")

        resp = post_worker_sync(
            ctx["url"],
            LEAD_WORKER,
            executor_report=make_executor_report(lead_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_stop_notifies_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        stop_resp = stop_session(ctx["url"], child_id)
        assert stop_resp.status_code == 200

        assert_session_state(ctx["db_url"], child_id, desired="stop")

        assert get_task_queue_count(ctx["db_url"], lead_id) == 1

        resp = post_worker_sync(
            ctx["url"],
            CHILD_WORKER,
            executor_report=make_executor_report(child_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            CHILD_WORKER,
            executor_report=make_executor_report(child_id, "idle"),
            command_ack=stop_token,
        )

        assert_session_state(
            ctx["db_url"], child_id, desired="stop", executor_state="idle"
        )


def _stop_and_ack_lead(ctx, lead_id):
    stop_resp = stop_session(ctx["url"], lead_id)
    assert stop_resp.status_code == 200

    resp = post_worker_sync(
        ctx["url"],
        LEAD_WORKER,
        executor_report=make_executor_report(lead_id, "running"),
    )
    assert resp["type"] == "command"
    assert resp["action"]["type"] == "stop_turn"
    stop_token = resp["token"]

    post_worker_sync(
        ctx["url"],
        LEAD_WORKER,
        executor_report=make_executor_report(lead_id, "idle"),
        command_ack=stop_token,
    )

    assert_session_state(ctx["db_url"], lead_id, desired="stop", executor_state="idle")


def _assert_state_change_event(db_url, session_id, desired_by_substring):
    assert_event_exists(db_url, session_id, "state_change", desired_by_substring)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_auto_resume_state_change__turn_complete(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        _stop_and_ack_lead(ctx, lead_id)

        post_worker_sync(
            ctx["url"],
            CHILD_WORKER,
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "child done"}]},
                ],
            ),
        )

        assert_session_state(ctx["db_url"], lead_id, desired="run")
        _assert_state_change_event(
            ctx["db_url"], lead_id, "system:turn_complete_notify"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_auto_resume_state_change__child_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        _stop_and_ack_lead(ctx, lead_id)

        stop_resp = stop_session(ctx["url"], child_id)
        assert stop_resp.status_code == 200

        assert_session_state(ctx["db_url"], lead_id, desired="run")
        _assert_state_change_event(ctx["db_url"], lead_id, "system:child_action_notify")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_auto_resume_state_change__child_crash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        _stop_and_ack_lead(ctx, lead_id)

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            executor_state="unassigned",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
            parent_notified=False,
            worker_id=None,
            command_token=None,
        )

        post_worker_sync(ctx["url"], "w-reconcile-trigger")

        assert_session_state(ctx["db_url"], lead_id, desired="run")
        _assert_state_change_event(ctx["db_url"], lead_id, "system:child_crash_notify")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_auto_resume_state_change__continue_from(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        _stop_and_ack_lead(ctx, lead_id)

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
            worker_id=None,
            command_token=None,
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/continue",
            json={"parts": [{"text": "continue working"}]},
            timeout=5,
        )
        assert resp.status_code == 201, f"continue_from failed: {resp.text}"

        assert_session_state(ctx["db_url"], lead_id, desired="run")
        _assert_state_change_event(
            ctx["db_url"], lead_id, "system:continue_from_notify"
        )
