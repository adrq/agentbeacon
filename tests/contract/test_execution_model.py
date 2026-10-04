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
    assert_execution_state,
    create_child_session_raw,
    get_execution_row,
    get_session_row,
    get_task_queue_count,
    make_executor_report,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


WORKER_ID = "test-worker-1"


def _get_execution_display(url, exec_id, timeout=5):
    resp = httpx.get(f"{url}/api/v1/executions/{exec_id}", timeout=timeout)
    assert resp.status_code == 200
    return resp.json()


def _terminate_execution(url, exec_id, timeout=5):
    return httpx.post(f"{url}/api/v1/executions/{exec_id}/terminate", timeout=timeout)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_new_execution_no_active_sessions_awaiting_input(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        data = _get_execution_display(ctx["url"], exec_id)
        assert data["execution"]["status"] == "awaiting_input"

        assert_execution_state(ctx["db_url"], exec_id, desired="run", outcome=None)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_running_execution_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

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

        data = _get_execution_display(ctx["url"], exec_id)
        assert data["execution"]["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminated_execution_shows_outcome(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )

        data = _get_execution_display(ctx["url"], exec_id)
        assert data["execution"]["status"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_gap_shows_canceled_fallback(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_execution_fields(ctx["db_url"], exec_id, desired="terminate")

        data = _get_execution_display(ctx["url"], exec_id)
        assert data["execution"]["status"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_one_terminate_action_derives_outcome(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        resp = _terminate_execution(ctx["url"], exec_id)
        assert resp.status_code == 200

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["desired"] == "terminate"
        assert row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_completion_eligible_all_quiescent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        data = _get_execution_display(ctx["url"], exec_id)
        assert data["execution"]["status"] == "awaiting_input"

        row = get_session_row(ctx["db_url"], root_sid)
        assert row["desired"] == "run"
        assert row["executor_state"] == "idle"
        assert get_task_queue_count(ctx["db_url"], root_sid) == 0
        assert row["command_token"] is None

        resp = _terminate_execution(ctx["url"], exec_id)
        assert resp.status_code == 200

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_not_eligible_crashed_child_shows_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="crashed",
        )

        child_row = get_session_row(ctx["db_url"], child_sid)
        assert child_row["desired"] == "run"
        assert child_row["executor_state"] == "crashed"

        resp = _terminate_execution(ctx["url"], exec_id)
        assert resp.status_code == 200

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_session_terminate_updates_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        httpx.post(f"{ctx['url']}/api/v1/sessions/{root_sid}/terminate", timeout=5)

        root_row = get_session_row(ctx["db_url"], root_sid)
        assert root_row["desired"] == "terminate"
        assert root_row["outcome"] == "completed"

        exec_row = get_execution_row(ctx["db_url"], exec_id)
        assert exec_row["desired"] == "terminate"
        assert exec_row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciler_repairs_execution_from_root_outcome(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            outcome="completed",
            executor_state="idle",
        )

        post_worker_sync(ctx["url"], "reconcile-worker")

        exec_row = get_execution_row(ctx["db_url"], exec_id)
        assert exec_row["desired"] == "terminate"
        assert exec_row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_execution_auto_completion(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        post_worker_sync(ctx["url"], "idle-worker")
        post_worker_sync(ctx["url"], "idle-worker")

        assert_execution_state(ctx["db_url"], exec_id, desired="run", outcome=None)

        data = _get_execution_display(ctx["url"], exec_id)
        assert data["execution"]["status"] == "awaiting_input"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciler_repairs_desired_terminate_missing_outcome(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            executor_state="idle",
        )

        post_worker_sync(ctx["url"], "repair-worker")

        root_row = get_session_row(ctx["db_url"], root_sid)
        assert root_row["desired"] == "terminate"
        assert root_row["outcome"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciler_repairs_outcome_set_desired_not_terminate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="run",
            outcome="completed",
            executor_state="idle",
        )

        post_worker_sync(ctx["url"], "repair-worker")

        root_row = get_session_row(ctx["db_url"], root_sid)
        assert root_row["desired"] == "terminate"
        assert root_row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciler_repairs_inconsistent_sessions(test_database):
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
            executor_state="idle",
            worker_id="w-idle",
        )

        set_session_fields(ctx["db_url"], child_a, outcome="completed")
        set_session_fields(ctx["db_url"], child_b, outcome="failed")

        post_worker_sync(ctx["url"], "tick-worker-1")

        row_a = get_session_row(ctx["db_url"], child_a)
        row_b = get_session_row(ctx["db_url"], child_b)

        assert row_a["desired"] == "terminate" or row_b["desired"] == "terminate"

        if not (row_a["desired"] == "terminate" and row_b["desired"] == "terminate"):
            post_worker_sync(ctx["url"], "tick-worker-2")

        row_a = get_session_row(ctx["db_url"], child_a)
        row_b = get_session_row(ctx["db_url"], child_b)
        assert row_a["desired"] == "terminate"
        assert row_b["desired"] == "terminate"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_session_repair_updates_execution_atomically(test_database):
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
            outcome="completed",
            executor_state="idle",
        )

        post_worker_sync(ctx["url"], "repair-worker")

        root_row = get_session_row(ctx["db_url"], root_sid)
        exec_row = get_execution_row(ctx["db_url"], exec_id)

        assert root_row["desired"] == "terminate"
        assert exec_row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_backup_cascade_from_workerless_terminal_root(test_database):
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
            worker_id="child-worker",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            outcome="completed",
            executor_state="idle",
            worker_id=None,
        )
        set_execution_fields(
            ctx["db_url"],
            exec_id,
            desired="terminate",
            outcome="completed",
        )

        child_before = get_session_row(ctx["db_url"], child_sid)
        assert child_before["desired"] == "run"

        post_worker_sync(ctx["url"], "unrelated-worker")

        child_after = get_session_row(ctx["db_url"], child_sid)
        assert child_after["desired"] == "terminate", (
            "Child should have desired=terminate after cascade from terminal root"
        )
        assert child_after["outcome"] is not None, (
            "Child should have outcome locked by cascade"
        )
