# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    create_child_session_raw,
    get_session_row,
    insert_task,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_quiescent_child_completes_it(test_database):
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

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["released"] is True

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_cascades_to_grandchildren(test_database):
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
        gc_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        _result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        for _ in range(20):
            gc_row = get_session_row(ctx["db_url"], gc_id)
            if gc_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], "cascade-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], gc_id, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_cascade_running_child_gets_canceled(test_database):
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
        gc_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )

        mcp_tools_call(ctx["url"], lead_sid, "release", {"session_id": child_id})

        for _ in range(20):
            gc_row = get_session_row(ctx["db_url"], gc_id)
            if gc_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], "cascade-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], gc_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_cascade_unassigned_child_gets_canceled(test_database):
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
        gc_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        set_session_fields(ctx["db_url"], gc_id, agent_session_id="sdk-orphaned")

        mcp_tools_call(ctx["url"], lead_sid, "release", {"session_id": child_id})

        assert_session_state(
            ctx["db_url"], gc_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_requires_parent_authority(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        other_parent_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        child_id = create_child_session_raw(
            ctx["db_url"],
            other_parent_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        data = mcp_call(
            ctx["url"],
            lead_sid,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": child_id}},
        )

        assert data["error"]["code"] == -32602
        assert "not a child" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_rejects_sibling(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        sibling_a = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        sibling_b = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        data = mcp_call(
            ctx["url"],
            sibling_a,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": sibling_b}},
        )

        assert data["error"]["code"] == -32602
        assert "not a child" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_running_child_cancels_it(test_database):
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

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["released"] is True

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_delegated_unassigned_child_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        insert_task(ctx["db_url"], exec_id, child_id, "delegated prompt")

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="canceled"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_working_root_with_mixed_descendants(test_database):
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
        gc_running = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
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
        gc_completed = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        mcp_tools_call(ctx["url"], lead_sid, "release", {"session_id": child_id})

        for _ in range(20):
            r1 = get_session_row(ctx["db_url"], gc_running)
            r2 = get_session_row(ctx["db_url"], gc_idle)
            if r1.get("desired") == "terminate" and r2.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], "cascade-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], gc_running, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], gc_idle, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"], gc_completed, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("terminal_outcome", ["completed", "failed", "canceled"])
@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_idempotent_on_already_terminal(test_database, terminal_outcome):
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
            outcome=terminal_outcome,
        )

        data = mcp_call(
            ctx["url"],
            lead_sid,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": child_id}},
        )

        assert "result" in data, f"Expected success, got: {data}"
        result = data["result"]
        assert result["isError"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_skips_already_terminal_descendants(test_database):
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
        completed_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        failed_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        assert json.loads(result["content"][0]["text"]) == {"released": True}
        assert_session_state(
            ctx["db_url"], child_id, desired="terminate", outcome="completed"
        )
        assert_session_state(
            ctx["db_url"],
            completed_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        assert_session_state(
            ctx["db_url"],
            failed_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_returns_correct_count(test_database):
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
        create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["released"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sub_lead_cannot_release_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": lead_id}},
        )

        assert data["error"]["code"] == -32602
        assert "not a child" in data["error"]["message"]
