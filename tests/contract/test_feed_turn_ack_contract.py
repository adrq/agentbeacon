# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    post_user_message,
    scheduler_context,
    seed_test_agent,
    stop_session,
)
from tests.mock_agent_helpers import (
    assert_no_command,
    assert_session_state,
    get_session_row,
    insert_task,
    make_executor_report,
    make_turn_result,
    set_session_fields,
    post_worker_sync,
)


WORKER = "test-worker-feed"


def _setup_idle_session(ctx):
    agent_id = seed_test_agent(ctx["db_url"])
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

    resp = post_worker_sync(ctx["url"], WORKER)
    assert resp["type"] == "command"
    assert resp["action"]["type"] == "assign"
    assign_token = resp["token"]

    post_worker_sync(
        ctx["url"],
        WORKER,
        executor_report=make_executor_report(
            session_id, "running", agent_session_id="sdk-1"
        ),
        command_ack=assign_token,
    )

    post_worker_sync(
        ctx["url"],
        WORKER,
        executor_report=make_executor_report(session_id, "idle"),
        turn_result=make_turn_result(
            session_id,
            messages=[{"role": "ROLE_AGENT", "parts": [{"text": "done"}]}],
        ),
    )

    assert_session_state(ctx["db_url"], session_id, executor_state="idle")
    return exec_id, session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_feed_turn_command_stays_pending_until_acked(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_idle_session(ctx)

        insert_task(ctx["db_url"], exec_id, session_id, "follow-up message")

        resp = post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"
        feed_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_token"] == feed_token
        assert row["command_type"] == "feed_turn"

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=feed_token,
        )

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_feed_turn_ack_withheld_on_crash_triggers_recovery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_idle_session(ctx)

        insert_task(ctx["db_url"], exec_id, session_id, "follow-up message")

        resp = post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "crashed"),
            turn_result=make_turn_result(
                session_id,
                error="SDK rejected message",
                error_kind="executor_failed",
            ),
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "crashed"

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(ctx["url"], WORKER)

        resp = post_worker_sync(ctx["url"], WORKER)
        assert resp["type"] == "command", (
            f"Expected assign command after crash recovery, got: {resp}"
        )
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["resume"] is True, (
            f"Expected resume=true on recovery assign, got: {resp['action']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_overrides_pending_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_idle_session(ctx)

        insert_task(ctx["db_url"], exec_id, session_id, "follow-up message")

        resp = post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"
        feed_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )

        resp = post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "cancel"
        cancel_token = resp["token"]
        assert cancel_token != feed_token

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=cancel_token,
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_token"] is None
        assert row["outcome"] == "canceled"


def _setup_stopped_session(ctx):
    agent_id = seed_test_agent(ctx["db_url"])
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

    resp = post_worker_sync(ctx["url"], WORKER)
    assert resp["type"] == "command"
    assert resp["action"]["type"] == "assign"
    assign_token = resp["token"]

    post_worker_sync(
        ctx["url"],
        WORKER,
        executor_report=make_executor_report(
            session_id, "running", agent_session_id="sdk-1"
        ),
        command_ack=assign_token,
    )

    stop_resp = stop_session(ctx["url"], session_id)
    assert stop_resp.status_code == 200

    resp = post_worker_sync(
        ctx["url"],
        WORKER,
        executor_report=make_executor_report(session_id, "running"),
    )
    assert resp["type"] == "command"
    assert resp["action"]["type"] == "stop_turn"
    stop_token = resp["token"]

    post_worker_sync(
        ctx["url"],
        WORKER,
        executor_report=make_executor_report(session_id, "idle"),
        command_ack=stop_token,
    )

    assert_session_state(
        ctx["db_url"], session_id, desired="stop", executor_state="idle"
    )
    return exec_id, session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_resume_feed_turn_acked(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_stopped_session(ctx)

        msg_resp = post_user_message(ctx["url"], session_id, "resume work")
        assert msg_resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="run")

        resp = post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"
        feed_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=feed_token,
        )

        assert_no_command(ctx["db_url"], session_id)
        assert_session_state(
            ctx["db_url"], session_id, desired="run", executor_state="running"
        )

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(
                session_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "resumed output"}]},
                ],
            ),
        )

        assert_session_state(
            ctx["db_url"], session_id, desired="run", executor_state="idle"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stuck_feed_turn_after_resume_times_out(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id = _setup_stopped_session(ctx)

        msg_resp = post_user_message(ctx["url"], session_id, "resume work")
        assert msg_resp.status_code == 200

        resp = post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "idle"),
        )
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(
            ctx["url"],
            WORKER,
            executor_report=make_executor_report(session_id, "running"),
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "crashed", (
            f"Expected crashed after feed_turn timeout, got: {row['executor_state']}"
        )
