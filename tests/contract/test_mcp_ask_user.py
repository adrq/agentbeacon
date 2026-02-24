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
            {
                "questions": [{"question": "JWT or session cookies?"}],
                "importance": "blocking",
            },
        )

        content = result["content"]
        assert len(content) == 1
        payload = json.loads(content[0]["text"])
        assert "question_ids" in payload

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
            {"questions": [{"question": "Which approach?"}], "importance": "blocking"},
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
            {"questions": [{"question": "Which approach?"}]},
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
            {
                "questions": [{"question": "FYI: started auth module"}],
                "importance": "fyi",
            },
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
            {"questions": [{"question": "FYI: making progress"}], "importance": "fyi"},
        )

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? AND event_type = 'platform'",
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
            {"questions": [{"question": "JWT or cookies?"}], "importance": "fyi"},
        )
        assert result.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_cannot_call_ask_user(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {"questions": [{"question": "hello?"}]},
            },
        )

        assert "error" in data
        assert data["error"]["code"] == -32600


# --- Type-mismatch validation (jsonschema enforcement) ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_options_wrong_type_rejected(test_database):
    """options present but not an array should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {
                    "questions": [{"question": "Pick one?", "options": "oops"}],
                },
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_context_wrong_type_rejected(test_database):
    """context present but not a string should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {
                    "questions": [{"question": "Pick one?", "context": 42}],
                },
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_importance_wrong_type_rejected(test_database):
    """importance present but not a valid enum value should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {
                    "questions": [{"question": "Pick one?"}],
                    "importance": "urgent",
                },
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_agent_wrong_type_rejected(test_database):
    """delegate with non-string agent should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": 123, "prompt": "do stuff"},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_message_wrong_type_rejected(test_database):
    """handoff with non-string message should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "handoff",
                "arguments": {"message": ["not", "a", "string"]},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


# --- Boundary constraint tests (minItems/maxItems, required fields) ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_empty_questions_rejected(test_database):
    """Empty questions array should be rejected by minItems: 1."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {"questions": []},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_too_many_questions_rejected(test_database):
    """More than 4 questions should be rejected by maxItems: 4."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        questions = [{"question": f"Q{i}?"} for i in range(5)]
        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {"questions": questions},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_single_option_rejected(test_database):
    """Options array with 1 item should be rejected by minItems: 2."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {
                    "questions": [
                        {
                            "question": "Pick?",
                            "options": [{"label": "A", "description": "only one"}],
                        }
                    ]
                },
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_missing_questions_rejected(test_database):
    """Missing required 'questions' field should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {"importance": "blocking"},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_missing_required_field_rejected(test_database):
    """delegate with missing 'agent' field should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"prompt": "do stuff"},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_missing_message_rejected(test_database):
    """handoff with no arguments should be rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "handoff",
                "arguments": {},
            },
        )
        assert "error" in data
        assert data["error"]["code"] == -32602
