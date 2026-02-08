"""Contract tests for MCP tools/list with role-based filtering."""

import sqlite3
import uuid

from tests.testhelpers import (
    create_execution_via_api,
    mcp_call,
    mcp_tools_call,
    mcp_tools_list,
    scheduler_context,
    seed_test_agent,
)


def _create_child_session(ctx, agent_id):
    """Helper: create execution + child session, return (exec_id, master_id, child_id)."""
    exec_id, master_id = create_execution_via_api(ctx["url"], agent_id, "test task")
    child_id = str(uuid.uuid4())
    conn = sqlite3.connect(ctx["db_path"])
    conn.execute(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
        (child_id, exec_id, master_id, agent_id),
    )
    conn.commit()
    conn.close()
    return exec_id, master_id, child_id


def test_master_session_gets_delegate_and_ask_user():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert sorted(tools) == ["ask_user", "delegate", "next_instruction"]


def test_master_does_not_get_handoff():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert "handoff" not in tools


def test_child_session_gets_handoff_and_next_instruction():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, _, child_id = _create_child_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], child_id)
        assert sorted(tools) == ["handoff", "next_instruction"]


def test_child_does_not_get_delegate_or_ask_user():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, _, child_id = _create_child_session(ctx, agent_id)

        tools = mcp_tools_list(ctx["url"], child_id)
        assert "delegate" not in tools
        assert "ask_user" not in tools


def test_tool_schemas_have_required_fields():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
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


def test_delegate_schema_has_agent_and_prompt():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = {t["name"]: t for t in data["result"]["tools"]}

        delegate = tools["delegate"]
        props = delegate["inputSchema"]["properties"]
        assert "agent" in props
        assert "prompt" in props
        assert sorted(delegate["inputSchema"]["required"]) == ["agent", "prompt"]


def test_ask_user_schema_has_question():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = {t["name"]: t for t in data["result"]["tools"]}

        ask = tools["ask_user"]
        props = ask["inputSchema"]["properties"]
        assert "question" in props
        assert ask["inputSchema"]["required"] == ["question"]


def test_tools_list_response_follows_mcp_spec_shape():
    """Verify the tools/list response has the right JSON-RPC + MCP shape."""
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")

        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert "result" in data
        assert "tools" in data["result"]
        assert isinstance(data["result"]["tools"], list)


def test_tool_definitions_include_title():
    """2025-11-25 spec: tools should include a human-readable title."""
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "tools/list")
        tools = data["result"]["tools"]

        for tool in tools:
            assert "title" in tool, f"Tool {tool['name']} missing 'title'"
            assert isinstance(tool["title"], str)
            assert len(tool["title"]) > 0


def test_tools_call_success_includes_is_error_false():
    """MCP spec: tool call results should include isError: false on success."""
    with scheduler_context() as ctx:
        master_agent_id = seed_test_agent(ctx["db_path"], name="master-agent")
        seed_test_agent(ctx["db_path"], name="child-agent")
        _, session_id = create_execution_via_api(
            ctx["url"], master_agent_id, "test task"
        )

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "do work"},
        )
        assert result.get("isError") is False
