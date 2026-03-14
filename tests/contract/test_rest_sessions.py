"""Contract tests for REST session endpoints."""

import json

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


# --- GET /api/sessions/{id} tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_session_by_id(test_database):
    """GET /api/sessions/{id} returns session detail."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )

        resp = httpx.get(f"{ctx['url']}/api/sessions/{session_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session_id
        assert data["execution_id"] == exec_id
        assert data["agent_id"] == agent_id
        assert data["status"] == "submitted"
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_session_nonexistent_returns_404(test_database):
    """GET /api/sessions/{id} returns 404 for unknown ID."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/sessions/nonexistent-id", timeout=5)
        assert resp.status_code == 404


# --- list sessions tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_sessions_returns_all(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        create_execution_via_api(ctx["url"], agent_id, "task 1")
        create_execution_via_api(ctx["url"], agent_id, "task 2")

        resp = httpx.get(f"{ctx['url']}/api/sessions", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_sessions_filter_by_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "test?"}], "importance": "blocking"},
        )

        resp = httpx.get(
            f"{ctx['url']}/api/sessions",
            params={"status": "input-required"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_sessions_filter_by_execution_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec1_id, _ = create_execution_via_api(ctx["url"], agent_id, "task 1")
        create_execution_via_api(ctx["url"], agent_id, "task 2")

        resp = httpx.get(
            f"{ctx['url']}/api/sessions",
            params={"execution_id": exec1_id},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["execution_id"] == exec1_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_events_returns_chronological(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "q1?"}], "importance": "fyi"},
        )

        resp = httpx.get(f"{ctx['url']}/api/sessions/{session_id}/events", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

        for event in data:
            assert "id" in event
            assert "event_type" in event
            assert "payload" in event
            assert "created_at" in event


# --- /message endpoint tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_input_required_session_returns_200(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "JWT"}]},
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_transitions_session_to_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "which approach?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "option A"}]},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert row[0] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_transitions_execution_to_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "which approach?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "option A"}]},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()
        assert row[0] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_non_input_required_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "answer"}]},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_full_ask_answer_round_trip(test_database):
    """End-to-end: escalate -> input-required -> answer -> working -> events verified."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "design auth"
        )

        # Execution must be working for CAS to transition to input-required
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'working' WHERE id = ?", (exec_id,)
            )
            conn.execute(
                "UPDATE sessions SET status = 'working' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        # 1. Ask a question (blocking)
        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {
                "questions": [
                    {
                        "question": "JWT or session cookies?",
                        "options": [
                            {"label": "JWT", "description": "Stateless tokens"},
                            {"label": "Cookies", "description": "Server-side sessions"},
                        ],
                    },
                ],
                "importance": "blocking",
            },
        )
        json.loads(result["content"][0]["text"])["question_ids"]

        # 2. Verify states
        with db_conn(ctx["db_url"]) as conn:
            session_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
            exec_status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert session_status == "input-required"
        assert exec_status == "input-required"

        # 3. Answer the question
        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "JWT"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        # 4. Verify states transitioned back
        with db_conn(ctx["db_url"]) as conn:
            session_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
            exec_status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert session_status == "working"
        assert exec_status == "working"

        # 5. Verify event chain
        resp = httpx.get(f"{ctx['url']}/api/sessions/{session_id}/events", timeout=5)
        events = resp.json()
        event_types = [e["event_type"] for e in events]
        assert event_types.count("platform") == 1
        assert event_types.count("message") == 2  # initial prompt + user answer
        assert event_types.count("state_change") >= 2

        # 6. Verify user message event payload shape (no question_event_id)
        # Skip the initial prompt event (first message) and check the answer event
        msg_events = [e for e in events if e["event_type"] == "message"]
        user_events = [e for e in msg_events if e["payload"].get("role") == "user"]
        assert len(user_events) == 2  # initial prompt + user answer
        answer_event = user_events[1]  # second user event is the answer
        assert answer_event["payload"]["parts"][0]["kind"] == "text"
        assert "question_event_id" not in answer_event["payload"]


# --- /message endpoint: A2A task payload tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_message_pushes_a2a_task(test_database):
    """POST /message pushes an A2A message payload to the task queue."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "text", "text": "JWT"}]},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchall()

        # Find the user answer payload (skip the initial bootstrap task)
        payloads = [json.loads(r[0]) for r in rows]
        message_payloads = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(message_payloads) >= 1
        assert message_payloads[-1]["message"]["parts"][0]["text"] == "JWT"
