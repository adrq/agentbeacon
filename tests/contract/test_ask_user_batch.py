"""Contract tests for batch ask_user MCP tool."""

import json

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
def test_ask_user_single_question_in_array(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"questions": [{"question": "JWT or cookies?"}]},
        )

        content = result["content"]
        payload = json.loads(content[0]["text"])
        assert "question_ids" in payload
        assert len(payload["question_ids"]) == 1
        assert "batch_id" in payload


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_batch_creates_multiple_events(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {
                "questions": [
                    {"question": "Q1?"},
                    {"question": "Q2?"},
                    {"question": "Q3?"},
                ],
            },
        )

        payload = json.loads(result["content"][0]["text"])
        assert len(payload["question_ids"]) == 3

        # Verify events share batch_id and have sequential batch_index
        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform' ORDER BY id",
                (session_id,),
            ).fetchall()

        ask_events = []
        for row in events:
            p = json.loads(row[0])
            if p.get("parts", [{}])[0].get("data", {}).get("type") == "ask_user":
                ask_events.append(p)

        assert len(ask_events) == 3
        batch_ids = set()
        indices = []
        for e in ask_events:
            data = e["parts"][0]["data"]
            batch_ids.add(data["batch_id"])
            indices.append(data["batch_index"])
        assert len(batch_ids) == 1
        assert indices == [0, 1, 2]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_batch_sets_input_required_once(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {
                "questions": [
                    {"question": "Q1?"},
                    {"question": "Q2?"},
                ],
            },
        )

        with db_conn(ctx["db_url"]) as conn:
            state_changes = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (session_id,),
            ).fetchall()

        # Only one state_change to input-required for the batch
        to_input_required = [
            r for r in state_changes if json.loads(r[0]).get("to") == "input-required"
        ]
        assert len(to_input_required) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_batch_with_options_and_context(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {
                "questions": [
                    {
                        "question": "Which auth?",
                        "context": "We need to choose an auth strategy",
                        "options": [
                            {"label": "JWT", "description": "Stateless tokens"},
                            {"label": "Cookies", "description": "Server-side sessions"},
                        ],
                    },
                ],
            },
        )

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform'",
                (session_id,),
            ).fetchall()

        ask_events = [
            json.loads(r[0])
            for r in events
            if json.loads(r[0]).get("parts", [{}])[0].get("data", {}).get("type")
            == "ask_user"
        ]
        data = ask_events[0]["parts"][0]["data"]
        assert data["context"] == "We need to choose an auth strategy"
        assert len(data["options"]) == 2
        assert data["options"][0]["label"] == "JWT"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_batch_fyi_does_not_change_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {
                "questions": [{"question": "FYI: progress update"}],
                "importance": "fyi",
            },
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "submitted"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_empty_questions_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={"name": "ask_user", "arguments": {"questions": []}},
        )

        assert "error" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_too_many_questions_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {
                    "questions": [{"question": f"Q{i}?"} for i in range(5)],
                },
            },
        )

        assert "error" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ask_user_options_require_label_and_description(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "ask_user",
                "arguments": {
                    "questions": [
                        {
                            "question": "Pick one",
                            "options": [
                                {"label": "A", "description": "Option A"},
                                {"label": "B"},
                            ],
                        },
                    ],
                },
            },
        )

        assert "error" in data
