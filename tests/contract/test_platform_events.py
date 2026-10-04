# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    assert_event_exists,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    create_child_session_raw,
    insert_task,
    make_executor_report,
    set_session_fields,
    post_worker_sync,
)


WORKER_ID = "test-worker-1"


def _count_events(db_url, session_id, event_type, payload_fragment=None):
    with db_conn(db_url) as conn:
        cur = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = ?",
            (session_id, event_type),
        )
        rows = cur.fetchall()
    if payload_fragment is None:
        return len(rows)
    return sum(1 for row in rows if payload_fragment in row[0])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_payload_timeout_assign_with_payload_emits_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "command"

        set_session_fields(
            ctx["db_url"],
            session_id,
            command_at="2020-01-01T00:00:00Z",
        )

        post_worker_sync(ctx["url"], "timeout-detector-worker")

        assert_event_exists(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="message may have been lost",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_payload_timeout_feed_turn_emits_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "initial")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="agent-1"
            ),
            command_ack=token,
        )

        insert_task(ctx["db_url"], exec_id, session_id, "second message")
        resp2 = post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp2["type"] == "command"

        set_session_fields(
            ctx["db_url"],
            session_id,
            command_at="2020-01-01T00:00:00Z",
        )

        post_worker_sync(ctx["url"], "timeout-detector-worker")

        assert_event_exists(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="message may have been lost",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_resume_without_payload_timeout_no_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        set_session_fields(
            ctx["db_url"], child_sid, agent_session_id="sdk-sess-1", cwd="/tmp/test-cwd"
        )

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "command"
        assert resp["action"]["session_id"] == child_sid

        set_session_fields(
            ctx["db_url"],
            child_sid,
            command_at="2020-01-01T00:00:00Z",
        )

        post_worker_sync(ctx["url"], "timeout-detector-worker")

        event_count = _count_events(
            ctx["db_url"],
            child_sid,
            "platform",
            payload_fragment="message may have been lost",
        )
        assert event_count == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_crash_clears_payload_command_emits_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "important message"
        )

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "command"

        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(session_id, "crashed"),
        )

        assert_event_exists(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="message may have been lost",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_crash_no_payload_command_no_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="agent-1"
            ),
            command_ack=token,
        )

        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(session_id, "crashed"),
        )

        event_count = _count_events(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="message may have been lost",
        )
        assert event_count == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_retry_with_agent_session_emits_may_have_been_lost_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], agent_type="claude_sdk")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="agent-1"
            ),
            command_ack=token,
        )
        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(session_id, "crashed"),
        )

        post_worker_sync(ctx["url"], "recovery-worker")

        assert_event_exists(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="work in progress may have been lost",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_retry_without_agent_session_emits_no_may_have_been_lost_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="crashed",
        )

        post_worker_sync(ctx["url"], "recovery-worker")

        event_count = _count_events(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="work in progress may have been lost",
        )
        assert event_count == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_permanent_failure_emits_failed_permanently_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="crashed",
        )
        set_session_fields(ctx["db_url"], child_sid, recovery_attempts=99)

        post_worker_sync(ctx["url"], "reconcile-worker")

        assert_event_exists(
            ctx["db_url"],
            child_sid,
            "platform",
            payload_fragment="failed permanently",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_override_discard_no_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"

        httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)

        resp2 = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp2["type"] == "command"
        assert resp2["action"]["type"] == "stop_turn"
        assert resp2["token"] != resp["token"]

        event_count = _count_events(
            ctx["db_url"],
            session_id,
            "platform",
            payload_fragment="message may have been lost",
        )
        assert event_count == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_no_message_loss_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            outcome="completed",
        )

        post_worker_sync(ctx["url"], "cascade-worker")

        event_count = _count_events(
            ctx["db_url"],
            child_sid,
            "platform",
            payload_fragment="message may have been lost",
        )
        assert event_count == 0
