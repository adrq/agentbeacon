# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_execution_state,
    assert_session_state,
    create_child_session_raw,
    get_session_row,
    insert_task,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_cancel_terminates_all_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child1 = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        child2 = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
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
            r1 = get_session_row(ctx["db_url"], child1)
            r2 = get_session_row(ctx["db_url"], child2)
            if r1.get("desired") == "terminate" and r2.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], lead_sid, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], child1, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], child2, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_cancel_with_deep_tree(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        grandchild = create_child_session_raw(
            ctx["db_url"],
            child,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        insert_task(ctx["db_url"], exec_id, grandchild, "delegated prompt")
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

        assert_execution_state(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], lead_sid, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], child, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], grandchild, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], great_grandchild, desired="terminate", outcome="completed"
        )
