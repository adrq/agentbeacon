# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import httpx
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
    create_child_session_raw,
    get_session_row,
    get_task_queue_count,
    insert_task,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_parent_terminate_cascades_to_children(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child_a = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        child_b = create_child_session_raw(
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

        for _ in range(40):
            row_a = get_session_row(ctx["db_url"], child_a)
            row_b = get_session_row(ctx["db_url"], child_b)
            if (
                row_a.get("desired") == "terminate"
                and row_b.get("desired") == "terminate"
            ):
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], child_a, desired="terminate")
        assert_session_state(ctx["db_url"], child_b, desired="terminate")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_quiescent_child_completed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
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

        for _ in range(40):
            child_row = get_session_row(ctx["db_url"], child)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_running_child_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
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

        for _ in range(40):
            child_row = get_session_row(ctx["db_url"], child)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_delegated_child_with_pending_work_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        insert_task(ctx["db_url"], exec_id, child, "delegated prompt")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            child_row = get_session_row(ctx["db_url"], child)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_skips_already_terminal(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child_completed = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        child_failed = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            row_c = get_session_row(ctx["db_url"], child_completed)
            row_f = get_session_row(ctx["db_url"], child_failed)
            if (
                row_c.get("desired") == "terminate"
                and row_f.get("desired") == "terminate"
            ):
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], child_completed, outcome="completed")
        assert_session_state(ctx["db_url"], child_failed, outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_never_produces_failed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child_running = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        child_crashed_run = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="crashed",
        )
        child_with_pending = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        insert_task(ctx["db_url"], exec_id, child_with_pending, "pending work")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            all_cascaded = all(
                get_session_row(ctx["db_url"], cid).get("desired") == "terminate"
                for cid in [child_running, child_crashed_run, child_with_pending]
            )
            if all_cascaded:
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        for child_id in [child_running, child_crashed_run, child_with_pending]:
            row = get_session_row(ctx["db_url"], child_id)
            assert row["outcome"] in ("completed", "canceled"), (
                f"session {child_id}: cascade produced outcome={row['outcome']!r}, "
                f"expected completed or canceled"
            )
            assert row["outcome"] != "failed", (
                f"session {child_id}: cascade must never produce 'failed'"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_deep_tree_cascade(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
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
            ctx["db_url"], root_sid, desired="terminate", outcome="completed"
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
def test_cascade_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
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

        for _ in range(40):
            child_row = get_session_row(ctx["db_url"], child)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="completed"
        )

        row_after_first = get_session_row(ctx["db_url"], child)

        post_worker_sync(ctx["url"], "idempotent-worker-1")
        post_worker_sync(ctx["url"], "idempotent-worker-2")

        row_after_second = get_session_row(ctx["db_url"], child)
        assert row_after_second["desired"] == row_after_first["desired"]
        assert row_after_second["outcome"] == row_after_first["outcome"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_tool_cascades_to_grandchildren(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="running",
            worker_id="worker-1",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
            ctx["db_url"],
            root_sid,
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

        _result = mcp_tools_call(
            ctx["url"],
            root_sid,
            "release",
            {"session_id": child},
        )

        for _ in range(40):
            gc_row = get_session_row(ctx["db_url"], grandchild)
            if gc_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], grandchild, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_finalization_order_cascade_drain_notify_release(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
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
            executor_state="crashed",
        )
        set_session_fields(ctx["db_url"], child, worker_id="worker-child")
        insert_task(ctx["db_url"], exec_id, child, "queued work to drain")

        grandchild = create_child_session_raw(
            ctx["db_url"],
            child,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        insert_task(ctx["db_url"], exec_id, grandchild, "delegated prompt")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            gc_row = get_session_row(ctx["db_url"], grandchild)
            child_row = get_session_row(ctx["db_url"], child)
            if (
                gc_row.get("desired") == "terminate"
                and child_row.get("worker_id") is None
            ):
                break
            post_worker_sync(ctx["url"], worker_id="finalize-trigger")
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], grandchild, desired="terminate")
        assert get_task_queue_count(ctx["db_url"], child) == 0
        row = get_session_row(ctx["db_url"], child)
        assert row["worker_id"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_finalize_clears_worker_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="crashed",
        )
        set_session_fields(ctx["db_url"], child, worker_id="worker-orphan")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200

        for _ in range(40):
            child_row = get_session_row(ctx["db_url"], child)
            if (
                child_row.get("desired") == "terminate"
                and child_row.get("worker_id") is None
            ):
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], child)
        assert row["desired"] == "terminate"
        assert row["worker_id"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_desired_by_system_cascade(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-agent")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        child = create_child_session_raw(
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

        for _ in range(40):
            child_row = get_session_row(ctx["db_url"], child)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], child)
        assert row["desired"] == "terminate"
        assert row["desired_by"] == "system:cascade"
