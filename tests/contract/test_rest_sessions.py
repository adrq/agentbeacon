"""Contract tests for REST session endpoints."""

import json
import sqlite3

import httpx

from tests.testhelpers import (
    create_execution_via_api,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def test_list_sessions_returns_all():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        create_execution_via_api(ctx["url"], agent_id, "task 1")
        create_execution_via_api(ctx["url"], agent_id, "task 2")

        resp = httpx.get(f"{ctx['url']}/api/sessions", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2


def test_list_sessions_filter_by_status():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "test?", "importance": "blocking"},
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


def test_list_sessions_filter_by_execution_id():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
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


def test_session_events_returns_chronological():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "q1?", "importance": "fyi"},
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


def test_answer_input_required_session_returns_200():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "JWT or cookies?", "importance": "blocking"},
        )

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "JWT"},
            timeout=5,
        )
        assert resp.status_code == 200


def test_answer_transitions_session_to_working():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "which approach?", "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "option A"},
            timeout=5,
        )

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "working"


def test_answer_transitions_execution_to_working():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "which approach?", "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "option A"},
            timeout=5,
        )

        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT status FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "working"


def test_answer_non_input_required_returns_409():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "answer"},
            timeout=5,
        )
        assert resp.status_code == 409


def test_full_ask_answer_round_trip():
    """End-to-end: ask_user -> input-required -> answer -> working -> events verified."""
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "design auth"
        )

        # 1. Ask a question (blocking)
        result = mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {
                "question": "JWT or session cookies?",
                "options": [
                    {"label": "JWT", "description": "Stateless tokens"},
                    {"label": "Cookies", "description": "Server-side sessions"},
                ],
                "importance": "blocking",
            },
        )
        json.loads(result["content"][0]["text"])["question_id"]

        # 2. Verify states
        conn = sqlite3.connect(ctx["db_path"])
        session_status = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
        exec_status = conn.execute(
            "SELECT status FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()[0]
        conn.close()
        assert session_status == "input-required"
        assert exec_status == "input-required"

        # 3. Answer the question
        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "JWT"},
            timeout=5,
        )
        assert resp.status_code == 200

        # 4. Verify states transitioned back
        conn = sqlite3.connect(ctx["db_path"])
        session_status = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
        exec_status = conn.execute(
            "SELECT status FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()[0]
        conn.close()
        assert session_status == "working"
        assert exec_status == "working"

        # 5. Verify event chain
        resp = httpx.get(f"{ctx['url']}/api/sessions/{session_id}/events", timeout=5)
        events = resp.json()
        event_types = [e["event_type"] for e in events]
        assert event_types.count("message") == 2
        assert event_types.count("state_change") >= 2
