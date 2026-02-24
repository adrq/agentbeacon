"""Contract tests for MCP handoff tool."""

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
def test_handoff_completes_child_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            child_id,
            "handoff",
            {"message": "auth implementation complete"},
        )

        content = result["content"]
        assert len(content) == 1
        payload = json.loads(content[0]["text"])
        assert payload["status"] == "completed"

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()
        assert row[0] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_records_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        mcp_tools_call(
            ctx["url"],
            child_id,
            "handoff",
            {"message": "work done"},
        )

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
                (child_id,),
            ).fetchall()

        event_types = [e[0] for e in events]
        assert "platform" in event_types
        assert "state_change" in event_types


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_success_includes_is_error_false(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            child_id,
            "handoff",
            {"message": "work done"},
        )
        assert result.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_lead_cannot_call_handoff(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "handoff",
                "arguments": {"message": "done"},
            },
        )

        assert "error" in data
        assert data["error"]["code"] == -32600
