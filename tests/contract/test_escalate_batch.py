# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate, rest_escalate_ok


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_single_question_in_array(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "JWT or cookies?"}]},
        )

        assert set(data) == {"batch_id", "event_id"}
        assert isinstance(data["event_id"], str)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_batch_is_one_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {"question": "Q1?"},
                    {"question": "Q2?"},
                    {"question": "Q3?"},
                ],
            },
        )

        assert isinstance(data["event_id"], str)

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform' ORDER BY id",
                (session_id,),
            ).fetchall()

        escalate_events = []
        for row in events:
            p = json.loads(row[0])
            if p.get("parts", [{}])[0].get("data", {}).get("type") == "escalate":
                escalate_events.append(p)

        assert len(escalate_events) == 1
        ev_data = escalate_events[0]["parts"][0]["data"]
        assert ev_data["batch_id"] == data["batch_id"]
        assert [q["question"] for q in ev_data["questions"]] == ["Q1?", "Q2?", "Q3?"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_batch_sets_input_required_once(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
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

        to_stop = [
            r for r in state_changes if json.loads(r[0]).get("desired") == "stop"
        ]
        assert len(to_stop) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_batch_with_options_and_context(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
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

        escalate_events = [
            json.loads(r[0])
            for r in events
            if json.loads(r[0]).get("parts", [{}])[0].get("data", {}).get("type")
            == "escalate"
        ]
        question = escalate_events[0]["parts"][0]["data"]["questions"][0]
        assert question["context"] == "We need to choose an auth strategy"
        assert len(question["options"]) == 2
        assert question["options"][0]["label"] == "JWT"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_batch_fyi_does_not_change_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [{"question": "FYI: progress update"}],
                "importance": "fyi",
            },
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT desired, executor_state FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row[0] == "run"
        assert row[1] == "unassigned"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_empty_questions_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"questions": []},
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_too_many_questions_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {
                "questions": [{"question": f"Q{i}?"} for i in range(5)],
            },
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_options_require_a_label(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Pick one",
                        "options": [
                            {"label": "A", "description": "Option A"},
                            {"description": "no label"},
                        ],
                    },
                ],
            },
        )
        assert resp.status_code == 400

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {
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
        )
        assert resp.status_code == 200, resp.text
