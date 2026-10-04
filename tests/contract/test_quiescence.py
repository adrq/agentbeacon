# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
import uuid
from datetime import datetime

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
def test_idle_no_pending_no_command_is_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"], sid, desired="run", executor_state="idle", worker_id="w-idle"
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_with_pending_turns_not_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"], sid, desired="run", executor_state="idle", worker_id="w-idle"
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")
        insert_task(ctx["db_url"], exec_id, sid, "pending work")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_with_pending_command_not_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
            command_token=str(uuid.uuid4()),
            command_type="feed_turn",
            command_has_payload=True,
            command_at=datetime.utcnow().isoformat(),
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_pristine_unassigned_is_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="unassigned",
            agent_session_id=None,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_non_pristine_unassigned_not_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="unassigned",
            agent_session_id="sdk-session-abc",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stopped_crashed_no_pending_is_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(ctx["db_url"], sid, desired="stop", executor_state="crashed")
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stopped_crashed_with_pending_turns_not_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"], session_id, desired="stop", executor_state="crashed"
        )
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200
        assert_session_state(
            ctx["db_url"], session_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crashed_with_desired_run_not_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        set_session_fields(ctx["db_url"], sid, desired="run", executor_state="crashed")
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_running_not_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], sid, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminating_execution_with_a_failed_root_ends_failed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="terminate")

        _child = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["execution"]["outcome"] == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminating_execution_with_an_active_session_ends_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")

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
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        _child_running = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminating_execution_with_a_non_quiescent_child_ends_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        _child_crashed = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="crashed",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminating_execution_with_an_unnotified_failed_child_ends_canceled(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")

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
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child_failed = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(ctx["db_url"], child_failed, parent_notified=False)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminating_execution_with_all_sessions_quiescent_ends_completed(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        _child_done = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        _child_idle = create_child_session_raw(
            ctx["db_url"],
            root_sid,
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

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_mixed_outcome_tree_worked_example(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
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

        child_a = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        child_b = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(ctx["db_url"], child_b, parent_notified=True)
        child_c = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(20):
            child_c_row = get_session_row(ctx["db_url"], child_c)
            if child_c_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], lead_sid, desired="terminate", outcome="completed"
        )
        assert_session_state(ctx["db_url"], child_a, outcome="completed")
        assert_session_state(ctx["db_url"], child_b, outcome="failed")
        assert_session_state(
            ctx["db_url"], child_c, desired="terminate", outcome="canceled"
        )
        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_vs_execution_outcome_divergence(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="quiescence-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        child = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        _grandchild = create_child_session_raw(
            ctx["db_url"],
            child,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"], root_sid, desired="terminate", outcome="completed"
        )
        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )
