# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    mcp_tools_list,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    create_child_session,
)


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


def _create_child_session(ctx, agent_id):
    exec_id, lead_id = _create_execution_with_limits(
        ctx, agent_id, "test task", max_depth=2
    )
    child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)
    return exec_id, lead_id, child_id


def _create_leaf_session(ctx, agent_id):
    exec_id, lead_id, child_id = _create_child_session(ctx, agent_id)
    leaf_id = create_child_session(ctx["db_url"], child_id, exec_id, agent_id)
    return exec_id, lead_id, child_id, leaf_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_lead_gets_delegate_release(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sub_lead_gets_delegate_release(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, _, child_id = _create_child_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], child_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_gets_no_tools(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, _, _, leaf_id = _create_leaf_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], leaf_id)
        assert tools == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tool_schemas_have_required_fields(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = data["result"]["tools"]

        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool}"
            assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]
            assert "required" in tool["inputSchema"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_schema_has_agent_and_prompt(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = {t["name"]: t for t in data["result"]["tools"]}

        delegate = tools["delegate"]
        props = delegate["inputSchema"]["properties"]
        assert "agent" in props
        assert "prompt" in props
        assert "session_id" not in props, "session_id should not be in delegate schema"
        assert sorted(delegate["inputSchema"]["required"]) == ["agent", "prompt"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tools_list_response_follows_mcp_spec_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")

        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert "result" in data
        assert "tools" in data["result"]
        assert isinstance(data["result"]["tools"], list)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tool_definitions_include_title(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = data["result"]["tools"]

        for tool in tools:
            assert "title" in tool, f"Tool {tool['name']} missing 'title'"
            assert isinstance(tool["title"], str)
            assert len(tool["title"]) > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tools_call_success_includes_is_error_false(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "test task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "do work"},
        )
        assert result.get("isError") is False
