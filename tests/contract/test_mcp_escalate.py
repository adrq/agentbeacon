# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate, rest_escalate_ok


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_blocking_sets_session_input_required(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [{"question": "JWT or session cookies?"}],
                "importance": "blocking",
            },
        )

        assert data["event_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT desired, executor_state FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row[0] == "run"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_blocking_sets_execution_input_required(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET desired = 'run' WHERE id = ?", (exec_id,)
            )
            conn.execute(
                "UPDATE sessions SET desired = 'run', executor_state = 'running' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "Which approach?"}], "importance": "blocking"},
        )

        with db_conn(ctx["db_url"]) as conn:
            sess_row = conn.execute(
                "SELECT desired FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert sess_row[0] == "run"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_default_importance_is_blocking(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "Which approach?"}]},
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT desired, executor_state FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row[0] == "run"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_fyi_does_not_change_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [{"question": "FYI: started auth module"}],
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
def test_escalate_fyi_records_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "FYI: making progress"}], "importance": "fyi"},
        )

        with db_conn(ctx["db_url"]) as conn:
            events = conn.execute(
                "SELECT event_type, payload FROM events WHERE session_id = ? AND event_type = 'platform'",
                (session_id,),
            ).fetchall()
        assert len(events) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_success_returns_200(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"questions": [{"question": "JWT or cookies?"}], "importance": "fyi"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"batch_id", "event_id"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_rejected_for_sub_lead(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                'INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, sandbox_policy) VALUES (?, ?, ?, ?, \'{"fs_level":"unrestricted"}\')',
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        resp = rest_escalate(
            ctx["url"],
            child_id,
            {"questions": [{"question": "hello?"}]},
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_rejected_for_leaf(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        grandchild_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                'INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, sandbox_policy) VALUES (?, ?, ?, ?, \'{"fs_level":"unrestricted"}\')',
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.execute(
                'INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, sandbox_policy) VALUES (?, ?, ?, ?, \'{"fs_level":"unrestricted"}\')',
                (grandchild_id, exec_id, child_id, agent_id),
            )
            conn.commit()

        resp = rest_escalate(
            ctx["url"],
            grandchild_id,
            {"questions": [{"question": "hello?"}]},
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_options_wrong_type_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"questions": [{"question": "Pick one?", "options": "oops"}]},
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_importance_wrong_value_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {
                "questions": [{"question": "Pick one?"}],
                "importance": "urgent",
            },
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_empty_questions_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

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
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        questions = [{"question": f"Q{i}?"} for i in range(5)]
        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"questions": questions},
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_single_option_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Pick?",
                        "options": [{"label": "A", "description": "only one"}],
                    }
                ]
            },
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_missing_questions_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"importance": "blocking"},
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_context_wrong_type_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"questions": [{"question": "Pick one?", "context": 42}]},
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_option_missing_description_accepted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Pick?",
                        "options": [
                            {"label": "A", "description": "Option A"},
                            {"label": "B"},
                        ],
                    }
                ]
            },
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_missing_auth_returns_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/escalate",
            json={"questions": [{"question": "hello?"}]},
            timeout=5,
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_invalid_token_returns_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = rest_escalate(
            ctx["url"],
            str(uuid.uuid4()),
            {"questions": [{"question": "hello?"}]},
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_terminal_session_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'completed' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        resp = rest_escalate(
            ctx["url"],
            session_id,
            {"questions": [{"question": "hello?"}]},
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_agent_wrong_type_rejected(test_database):
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
def test_delegate_missing_required_field_rejected(test_database):
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
