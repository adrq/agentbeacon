# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_execution_state,
    assert_session_state,
    create_child_session_raw,
    get_session_row,
    insert_task,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_quiescent_tree_completes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["execution"]["outcome"] == "completed"
        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_running_session_cancels(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["execution"]["outcome"] == "canceled"
        assert_session_state(
            ctx["db_url"], lead_sid, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_already_terminal_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp1 = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp1.status_code == 200

        resp2 = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp2.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_pristine_completes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_cascade_by_quiescence(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        child_idle = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        child_running = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        child_unassigned_with_work = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        insert_task(
            ctx["db_url"], exec_id, child_unassigned_with_work, "delegated prompt"
        )
        child_already_completed = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            all_cascaded = all(
                get_session_row(ctx["db_url"], sid).get("desired") == "terminate"
                for sid in [child_idle, child_running, child_unassigned_with_work]
            )
            if all_cascaded:
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], lead_sid, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], child_idle, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], child_running, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"],
            child_unassigned_with_work,
            desired="terminate",
            outcome="canceled",
        )
        assert_session_state(
            ctx["db_url"],
            child_already_completed,
            desired="terminate",
            outcome="completed",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_deep_tree(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        grandchild = create_child_session_raw(
            ctx["db_url"],
            child,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        great_grandchild = create_child_session_raw(
            ctx["db_url"],
            grandchild,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            ggc_row = get_session_row(ctx["db_url"], great_grandchild)
            if ggc_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], lead_sid, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], grandchild, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], great_grandchild, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_emits_state_change_events(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        events_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=10
        )
        events = events_resp.json()["items"]

        exec_state_events = [
            e
            for e in events
            if e["event_type"] == "state_change" and e.get("session_id") is None
        ]
        assert len(exec_state_events) >= 1

        payload = exec_state_events[-1]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["desired"] == "terminate"
        assert payload["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_idempotent_after_completion(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        resp1 = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp1.status_code == 200
        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )

        resp2 = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp2.status_code == 200
        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminate_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="lead-agent")
        fake_id = str(uuid.uuid4())

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{fake_id}/terminate", timeout=10
        )
        assert resp.status_code == 404
