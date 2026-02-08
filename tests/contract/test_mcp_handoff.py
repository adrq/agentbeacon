"""Contract tests for MCP handoff tool."""

import json
import sqlite3
import uuid

from tests.testhelpers import (
    create_execution_via_api,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def test_handoff_completes_child_session():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, master_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
            (child_id, exec_id, master_id, agent_id),
        )
        conn.commit()
        conn.close()

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

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (child_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "completed"


def test_handoff_records_event():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, master_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
            (child_id, exec_id, master_id, agent_id),
        )
        conn.commit()
        conn.close()

        mcp_tools_call(
            ctx["url"],
            child_id,
            "handoff",
            {"message": "work done"},
        )

        conn = sqlite3.connect(ctx["db_path"])
        events = conn.execute(
            "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
            (child_id,),
        ).fetchall()
        conn.close()

        event_types = [e[0] for e in events]
        assert "message" in event_types
        assert "state_change" in event_types


def test_handoff_success_includes_is_error_false():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, master_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
            (child_id, exec_id, master_id, agent_id),
        )
        conn.commit()
        conn.close()

        result = mcp_tools_call(
            ctx["url"],
            child_id,
            "handoff",
            {"message": "work done"},
        )
        assert result.get("isError") is False


def test_master_cannot_call_handoff():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
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
