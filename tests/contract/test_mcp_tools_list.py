"""Contract tests for MCP tools/list with role-based filtering."""

import uuid

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


def _create_child_session(ctx, agent_id):
    """Helper: create execution + child session, return (exec_id, lead_id, child_id)."""
    exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
            (child_id, exec_id, lead_id, agent_id),
        )
        conn.commit()
    return exec_id, lead_id, child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_lead_session_gets_all_tools(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert sorted(tools) == [
            "delegate",
            "escalate",
            "handoff",
            "next_instruction",
            "release",
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_session_gets_escalate_handoff_next_instruction(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, _, child_id = _create_child_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], child_id)
        assert sorted(tools) == ["escalate", "handoff", "next_instruction"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_does_not_get_delegate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, _, child_id = _create_child_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], child_id)
        assert "delegate" not in tools


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
        assert sorted(delegate["inputSchema"]["required"]) == ["agent", "prompt"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_schema_has_questions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = {t["name"]: t for t in data["result"]["tools"]}

        escalate = tools["escalate"]
        props = escalate["inputSchema"]["properties"]
        assert "questions" in props
        assert escalate["inputSchema"]["required"] == ["questions"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tools_list_response_follows_mcp_spec_shape(test_database):
    """Verify the tools/list response has the right JSON-RPC + MCP shape."""
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
    """2025-11-25 spec: tools should include a human-readable title."""
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
    """MCP spec: tool call results should include isError: false on success."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        seed_test_agent(ctx["db_url"], name="child-agent")
        _, session_id = create_execution_via_api(ctx["url"], lead_agent_id, "test task")

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "do work"},
        )
        assert result.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_lead_tool_list_includes_handoff(test_database):
    """Lead sessions now include handoff in their tool list."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert "handoff" in tools


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_tool_list_includes_escalate(test_database):
    """Child sessions now include escalate in their tool list."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, _, child_id = _create_child_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], child_id)
        assert "escalate" in tools
