# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    get_task_queue_count,
    create_child_session_raw,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_creates_child_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )

        content = result["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        payload = json.loads(content[0]["text"])
        child_session_id = payload["session_id"]
        assert len(child_session_id) == 36


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_queues_task(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        assert get_task_queue_count(ctx["db_url"], child_session_id) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_ignores_unknown_session_id_param(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        bogus_id = "some-bogus-session-id"
        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {
                "agent": "child-agent",
                "prompt": "implement auth",
                "session_id": bogus_id,
            },
        )

        payload = json.loads(result["content"][0]["text"])
        assert payload["session_id"] != bogus_id
        assert len(payload["session_id"]) == 36


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_nonexistent_agent_returns_error(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "nonexistent", "prompt": "do work"},
            },
        )

        assert data["error"]["code"] == -32602
        assert "agent not found" in data["error"]["message"].lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_disabled_agent_returns_error(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        seed_test_agent(ctx["db_url"], name="disabled-agent", enabled=False)
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "disabled-agent", "prompt": "do work"},
            },
        )

        assert data["error"]["code"] == -32602
        assert "disabled" in data["error"]["message"].lower()


def _create_execution_with_limits(ctx, agent_id, prompt, max_depth=None):
    payload = {
        "root_agent_id": agent_id,
        "agent_ids": [agent_id],
        "parts": [{"text": prompt}],
        "title": prompt,
        "cwd": tempfile.gettempdir(),
    }
    if max_depth is not None:
        payload["max_depth"] = max_depth
    resp = httpx.post(f"{ctx['url']}/api/v1/executions", json=payload, timeout=10)
    assert resp.status_code == 201, f"Create execution failed: {resp.text}"
    data = resp.json()
    return data["execution"]["id"], data["session_id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_session_has_correct_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )

        payload = json.loads(result["content"][0]["text"])
        child_session_id = payload["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT execution_id, parent_session_id, agent_id, desired, executor_state, outcome FROM sessions WHERE id = ?",
                (child_session_id,),
            ).fetchone()

        assert row is not None
        assert row[0] == exec_id
        assert row[1] == lead_session_id
        assert row[2] == child_agent_id
        assert row[3] == "run"
        assert row[4] == "unassigned"
        assert row[5] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_cannot_call_delegate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=2
        )

        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )
        grandchild_id = create_child_session_raw(
            ctx["db_url"],
            child_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="unassigned",
        )

        data = mcp_call(
            ctx["url"],
            grandchild_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "claude-code", "prompt": "do work"},
            },
        )

        assert data["error"]["code"] == -32600
        assert "no tools available" in data["error"]["message"].lower()
