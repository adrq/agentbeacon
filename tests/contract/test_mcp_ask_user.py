"""Contract tests for MCP ask_user tool."""

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
def test_ask_user_blocking_sets_session_input_required(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
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

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_blocking_sets_execution_input_required(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "Which approach?", "importance": "blocking"},
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()
        assert row[0] == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_default_importance_is_blocking(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "Which approach?"},
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_fyi_does_not_change_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "FYI: started auth module", "importance": "fyi"},
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "submitted"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_fyi_records_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "FYI: making progress", "importance": "fyi"},
        )

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? AND event_type = 'message'",
                (session_id,),
            ).fetchall()
        assert len(events) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_success_includes_is_error_false(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "JWT or cookies?", "importance": "fyi"},
        )
        assert result.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_cannot_call_ask_user(test_database):
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
                "name": "ask_user",
                "arguments": {"question": "hello?"},
            },
        )

        assert "error" in data
        assert data["error"]["code"] == -32600
