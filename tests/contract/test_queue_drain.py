# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_command_pending,
    assert_session_state,
    get_session_row,
    get_task_queue_count,
    insert_task,
    set_session_fields,
    post_worker_sync,
)


WORKER_ID = "test-worker-qd"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_drains_task_queue(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            agent_session_id="agent-sess-1",
            worker_id="w-owner",
        )

        insert_task(ctx["db_url"], exec_id, session_id, "msg-1")
        insert_task(ctx["db_url"], exec_id, session_id, "msg-2")
        assert get_task_queue_count(ctx["db_url"], session_id) >= 3

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="stop")
        assert get_task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_resume_from_stop_preserves_queue(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="idle",
            agent_session_id="agent-sess-1",
        )
        assert get_task_queue_count(ctx["db_url"], session_id) == 0

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "continue"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="run")
        assert get_task_queue_count(ctx["db_url"], session_id) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_finalization_drains_task_queue(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id="agent-sess-1",
        )

        insert_task(ctx["db_url"], exec_id, session_id, "leftover-task")
        assert get_task_queue_count(ctx["db_url"], session_id) >= 2

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        post_worker_sync(ctx["url"], worker_id="finalize-trigger")
        for _ in range(20):
            if get_task_queue_count(ctx["db_url"], session_id) == 0:
                break
            post_worker_sync(ctx["url"], worker_id="finalize-trigger")
            time.sleep(0.25)

        assert get_task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_preserves_queue(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="crashed",
            agent_session_id="agent-sess-1",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )

        insert_task(ctx["db_url"], exec_id, session_id, "preserved-task")
        count_before = get_task_queue_count(ctx["db_url"], session_id)
        assert count_before >= 2

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert get_task_queue_count(ctx["db_url"], session_id) >= count_before


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_override_stop_over_feed_turn_discards_payload(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            agent_session_id="agent-sess-1",
            worker_id=WORKER_ID,
        )

        insert_task(ctx["db_url"], exec_id, session_id, "msg-to-discard")

        post_worker_sync(ctx["url"], worker_id=WORKER_ID)

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("command_token") is not None:
                break
            time.sleep(0.25)

        assert_command_pending(ctx["db_url"], session_id, "feed_turn", has_payload=True)

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 200

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("command_type") == "stop_turn":
                break
            post_worker_sync(ctx["url"], worker_id=WORKER_ID)
            time.sleep(0.25)

        assert_command_pending(
            ctx["db_url"], session_id, "stop_turn", has_payload=False
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_override_cancel_over_assign_discards_payload(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], worker_id=WORKER_ID)

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("command_token") is not None:
                break
            time.sleep(0.25)

        assert_command_pending(ctx["db_url"], session_id, "assign", has_payload=True)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("command_type") == "cancel":
                break
            post_worker_sync(ctx["url"], worker_id=WORKER_ID)
            time.sleep(0.25)

        assert_command_pending(ctx["db_url"], session_id, "cancel", has_payload=False)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_preserves_agent_session_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        original_agent_session = "sdk-session-to-preserve"
        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id=original_agent_session,
        )

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 200

        row = get_session_row(ctx["db_url"], session_id)
        assert row["agent_session_id"] == original_agent_session
        assert row["desired"] == "stop"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_atomic_pop_and_dispatch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id="agent-sess-1",
            worker_id=WORKER_ID,
        )

        insert_task(ctx["db_url"], exec_id, session_id, "deliver-me")
        assert get_task_queue_count(ctx["db_url"], session_id) == 1

        post_worker_sync(ctx["url"], worker_id=WORKER_ID)

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("command_token") is not None:
                break
            time.sleep(0.25)

        assert_command_pending(ctx["db_url"], session_id, "feed_turn", has_payload=True)
        assert get_task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_drain_removes_all_tasks(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            agent_session_id="agent-sess-1",
            worker_id="w-owner",
        )

        insert_task(ctx["db_url"], exec_id, session_id, "drain-1")
        insert_task(ctx["db_url"], exec_id, session_id, "drain-2")
        insert_task(ctx["db_url"], exec_id, session_id, "drain-3")
        assert get_task_queue_count(ctx["db_url"], session_id) >= 4

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], session_id) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_drain_idempotent_on_empty_queue(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id="agent-sess-1",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        assert get_task_queue_count(ctx["db_url"], session_id) == 0

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], session_id) == 0
        assert_session_state(ctx["db_url"], session_id, desired="stop")
