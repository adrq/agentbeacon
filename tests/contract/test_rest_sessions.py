# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    get_session_row,
    get_task_queue_count,
    rest_escalate_ok,
    set_execution_fields,
    set_session_fields,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_session_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_sessions_returns_all(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        create_execution_via_api(ctx["url"], agent_id, "task 1")
        create_execution_via_api(ctx["url"], agent_id, "task 2")

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_sessions_filter_by_execution_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec1_id, _ = create_execution_via_api(ctx["url"], agent_id, "task 1")
        create_execution_via_api(ctx["url"], agent_id, "task 2")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/sessions",
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

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "q1?"}], "importance": "fyi"},
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/{session_id}/events", timeout=5)
        assert resp.status_code == 200
        data = resp.json()["items"]
        assert len(data) >= 1

        for event in data:
            assert "id" in event
            assert "event_type" in event
            assert "payload" in event
            assert "created_at" in event


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_after_escalation_returns_200(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "JWT"}]},
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_execution_has_active_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "which approach?"}], "importance": "blocking"},
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "DELETE FROM task_queue WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()

        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "option A"}]},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            task_count = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        assert task_count == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_message_pushes_a2a_task(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "JWT or cookies?"}], "importance": "blocking"},
        )

        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "JWT"}]},
            timeout=5,
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchall()

        payloads = [json.loads(r[0]) for r in rows]
        message_payloads = [
            p
            for p in payloads
            if isinstance(p, dict) and "message" in p and "driver" not in p
        ]
        assert len(message_payloads) >= 1
        assert message_payloads[-1]["message"]["parts"][0]["text"] == "JWT"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_session_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/{session_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session_id
        assert data["execution_id"] == exec_id
        assert data["agent_id"] == agent_id
        assert data["desired"] == "run"
        assert data["executor_state"] == "unassigned"
        assert data["outcome"] is None
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalation_keeps_session_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "test?"}], "importance": "blocking"},
        )

        events_resp = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events",
            timeout=5,
        )
        assert events_resp.status_code == 200
        events = events_resp.json()["items"]
        has_escalation = any(e.get("event_type") == "escalate" for e in events)
        assert has_escalation, "escalation event should be present"

        row = get_session_row(ctx["db_url"], session_id)
        assert row["desired"] == "run"
        assert row["executor_state"] in ("unassigned", "running", "idle")

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/{session_id}", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_answer_enqueues_task_and_resumes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "which approach?"}], "importance": "blocking"},
        )

        set_session_fields(ctx["db_url"], session_id, desired="stop")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "option A"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        row = get_session_row(ctx["db_url"], session_id)
        assert row["desired"] == "run", "SendMessage should auto-resume stopped session"
        assert get_task_queue_count(ctx["db_url"], session_id) >= 1, (
            "answer should enqueue a task"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_message_to_live_session_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "DELETE FROM task_queue WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "new work"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        task_count = get_task_queue_count(ctx["db_url"], session_id)
        assert task_count == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_full_ask_answer_round_trip(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "design auth"
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            worker_id="w-running",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        result = rest_escalate_ok(
            ctx["url"],
            session_id,
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
        assert result["event_id"]

        row = get_session_row(ctx["db_url"], session_id)
        assert row["desired"] == "run"

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "JWT"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        row = get_session_row(ctx["db_url"], session_id)
        assert row["desired"] == "run"

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/{session_id}/events", timeout=5)
        events = resp.json()["items"]
        event_types = [e["event_type"] for e in events]
        assert event_types.count("platform") == 1
        assert event_types.count("message") == 2

        msg_events = [e for e in events if e["event_type"] == "message"]
        user_events = [e for e in msg_events if e["payload"].get("role") == "ROLE_USER"]
        assert len(user_events) == 2
        answer_event = user_events[1]
        assert "text" in answer_event["payload"]["parts"][0]
        assert "question_event_id" not in answer_event["payload"]
