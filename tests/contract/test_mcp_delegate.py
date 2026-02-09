"""Contract tests for MCP delegate tool."""

import json
import uuid

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_creates_child_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        master_agent_id = seed_test_agent(ctx["db_url"], name="master-agent")
        seed_test_agent(ctx["db_url"], name="child-agent")

        _, master_session_id = create_execution_via_api(
            ctx["url"], master_agent_id, "coordinate task"
        )

        result = mcp_tools_call(
            ctx["url"],
            master_session_id,
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
def test_delegate_child_session_has_correct_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        master_agent_id = seed_test_agent(ctx["db_url"], name="master-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, master_session_id = create_execution_via_api(
            ctx["url"], master_agent_id, "coordinate task"
        )

        result = mcp_tools_call(
            ctx["url"],
            master_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )

        payload = json.loads(result["content"][0]["text"])
        child_session_id = payload["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT execution_id, parent_session_id, agent_id, status FROM sessions WHERE id = ?",
                (child_session_id,),
            ).fetchone()

        assert row is not None
        assert row[0] == exec_id
        assert row[1] == master_session_id
        assert row[2] == child_agent_id
        assert row[3] == "submitted"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_queues_task(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        master_agent_id = seed_test_agent(ctx["db_url"], name="master-agent")
        seed_test_agent(ctx["db_url"], name="child-agent")

        _, master_session_id = create_execution_via_api(
            ctx["url"], master_agent_id, "coordinate task"
        )

        result = mcp_tools_call(
            ctx["url"],
            master_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()[0]
        assert count == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_with_resume_session_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        master_agent_id = seed_test_agent(ctx["db_url"], name="master-agent")
        seed_test_agent(ctx["db_url"], name="child-agent")

        _, master_session_id = create_execution_via_api(
            ctx["url"], master_agent_id, "coordinate task"
        )

        # First delegation
        result1 = mcp_tools_call(
            ctx["url"],
            master_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result1["content"][0]["text"])["session_id"]

        # Mark child completed
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed' WHERE id = ?",
                (child_session_id,),
            )
            conn.commit()

        # Resume with existing session_id
        result2 = mcp_tools_call(
            ctx["url"],
            master_session_id,
            "delegate",
            {
                "agent": "child-agent",
                "prompt": "continue work",
                "session_id": child_session_id,
            },
        )

        resumed_id = json.loads(result2["content"][0]["text"])["session_id"]
        assert resumed_id == child_session_id

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_session_id,)
            ).fetchone()
        assert row[0] == "submitted"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_nonexistent_agent_returns_error(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="master-agent")
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

        assert "error" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_disabled_agent_returns_error(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="master-agent")
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

        assert "error" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_cannot_call_delegate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, master_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_id, exec_id, master_id, agent_id),
            )
            conn.commit()

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "claude-code", "prompt": "do work"},
            },
        )

        assert "error" in data
        assert data["error"]["code"] == -32600
