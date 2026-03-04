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
                "SELECT execution_id, parent_session_id, agent_id, status FROM sessions WHERE id = ?",
                (child_session_id,),
            ).fetchone()

        assert row is not None
        assert row[0] == exec_id
        assert row[1] == lead_session_id
        assert row[2] == child_agent_id
        assert row[3] == "submitted"


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

        with db_conn(ctx["db_url"]) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()[0]
        assert count == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_ignores_unknown_session_id_param(test_database):
    """session_id parameter was removed — extra params are ignored by MCP."""
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

        # Pass session_id param — should be ignored, new session created
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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_cannot_call_delegate(test_database):
    """Leaf (depth >= max_depth) cannot call delegate."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        # depth 1 = SubLead (has delegate), depth 2 = Leaf (max_depth=2)
        child_id = str(uuid.uuid4())
        grandchild_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (grandchild_id, exec_id, child_id, agent_id),
            )
            conn.commit()

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
