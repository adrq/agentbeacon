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
    assert_session_state,
    create_child_session_raw,
    get_session_row,
    insert_task,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_session_cascades_to_children(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        gc_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        insert_task(ctx["db_url"], exec_id, gc_id, "delegated prompt")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            gc_row = get_session_row(ctx["db_url"], gc_id)
            if gc_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="canceled"
        )
        assert_session_state(ctx["db_url"], gc_id, desired="terminate")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_session_already_terminal_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=10
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_session_quiescent_completes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        gc_idle = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        gc_running = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            gc_idle_row = get_session_row(ctx["db_url"], gc_idle)
            gc_running_row = get_session_row(ctx["db_url"], gc_running)
            if (
                gc_idle_row.get("desired") == "terminate"
                and gc_running_row.get("desired") == "terminate"
            ):
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], gc_idle, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], gc_running, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_session_notifies_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
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
                (lead_sid,),
            ).fetchall()

        payloads = [r[0] for r in rows]
        notification = next((p for p in payloads if child_id in p), None)
        assert notification is not None, (
            f"No notification with child_id found in parent task_queue: {payloads}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_session_running_yields_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_session_idle_no_children_completes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
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

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="completed"
        )
