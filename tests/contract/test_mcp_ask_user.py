"""Contract tests for MCP ask_user tool."""

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


def test_ask_user_blocking_sets_session_input_required():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "JWT or session cookies?", "importance": "blocking"},
        )

        content = result["content"]
        assert len(content) == 1
        payload = json.loads(content[0]["text"])
        assert "question_id" in payload

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "input-required"


def test_ask_user_blocking_sets_execution_input_required():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "Which approach?", "importance": "blocking"},
        )

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "input-required"


def test_ask_user_default_importance_is_blocking():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "Which approach?"},
        )

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "input-required"


def test_ask_user_fyi_does_not_change_status():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "FYI: started auth module", "importance": "fyi"},
        )

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "submitted"


def test_ask_user_fyi_records_event():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "FYI: making progress", "importance": "fyi"},
        )

        conn = sqlite3.connect(ctx["db_path"])
        events = conn.execute(
            "SELECT event_type, payload FROM events WHERE session_id = ? AND event_type = 'message'",
            (session_id,),
        ).fetchall()
        conn.close()
        assert len(events) == 1


def test_ask_user_success_includes_is_error_false():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "JWT or cookies?", "importance": "fyi"},
        )
        assert result.get("isError") is False


def test_child_cannot_call_ask_user():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, master_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
            (child_id, exec_id, master_id, agent_id),
        )
        conn.commit()
        conn.close()

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {"question": "hello?"},
            },
        )

        assert "error" in data
        assert data["error"]["code"] == -32600
