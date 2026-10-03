# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _events(db_url, execution_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT id, execution_id, session_id, event_type FROM events "
            "WHERE execution_id = ? ORDER BY id ASC",
            (execution_id,),
        ).fetchall()
    return [
        {"id": r[0], "execution_id": r[1], "session_id": r[2], "event_type": r[3]}
        for r in rows
    ]


@DUAL_BACKEND
def test_execution_level_rows_are_ordinary(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="binding-exec-level")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200, resp.text

        execution_level = [
            e
            for e in _events(test_database, exec_id)
            if e["session_id"] is None and e["event_type"] == "state_change"
        ]
        assert execution_level, "no execution-level state_change row was written"
        for row in execution_level:
            assert row["execution_id"] == exec_id


@DUAL_BACKEND
def test_every_written_row_belongs_to_its_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="binding-coherent")
        exec_a, session_a = create_execution_via_api(ctx["url"], agent_id, "A")
        exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "B")

        for session_id in (session_a, session_b):
            rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": "Q?"}]}
            )
            httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "hello"}]},
                timeout=10,
            )

        with db_conn(test_database) as conn:
            incoherent = conn.execute(
                "SELECT e.id FROM events e JOIN sessions s ON s.id = e.session_id "
                "WHERE s.execution_id != e.execution_id"
            ).fetchall()
        assert incoherent == [], (
            "found events whose session belongs to a different execution"
        )

        for row in _events(test_database, exec_a):
            assert row["session_id"] in (None, session_a)
        for row in _events(test_database, exec_b):
            assert row["session_id"] in (None, session_b)


@DUAL_BACKEND
def test_answer_marker_resolved_at_is_the_message_rows_db_timestamp(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="binding-marker")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Which?"}]}
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "PostgreSQL"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": decision["event_id"],
                        }
                    },
                ]
            },
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        message_event_id = int(resp.json()["event_id"])

        events = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events", timeout=10
        ).json()["items"]
        marker = next(
            e
            for e in events
            if e["event_type"] == "platform"
            and e["payload"]["parts"][0]["data"].get("type") == "question_answer"
        )
        message = next(e for e in events if e["id"] == str(message_event_id))

        data = marker["payload"]["parts"][0]["data"]
        assert data["resolved_event_id"] == message_event_id
        assert data["resolved_at"].endswith("Z")
        assert data["resolved_at"] == message["created_at"].replace("+00:00", "Z")


@DUAL_BACKEND
def test_a_misdirected_marker_is_never_written(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="binding-misdirect")
        _exec_a, session_a = create_execution_via_api(ctx["url"], agent_id, "A")
        _exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "B")
        decision = rest_escalate_ok(
            ctx["url"], session_a, {"questions": [{"question": "Owned?"}]}
        )

        before = len(_events(test_database, _exec_b))
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_b}/message",
            json={
                "parts": [
                    {"text": "not mine"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": decision["event_id"],
                        }
                    },
                ]
            },
            timeout=10,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.wrong_session"
        assert len(_events(test_database, _exec_b)) == before

        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{decision['event_id']}", timeout=10
        ).json()
        assert detail["status"] == "pending"


@DUAL_BACKEND
def test_escalate_payload_is_written_as_one_row(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="binding-one-row")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        before = len(_events(test_database, exec_id))

        rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": f"Q{i}?"} for i in range(4)]},
        )

        after = _events(test_database, exec_id)
        assert len(after) == before + 1
        with db_conn(test_database) as conn:
            payload = conn.execute(
                "SELECT payload FROM events WHERE id = ?", (after[-1]["id"],)
            ).fetchone()[0]
        data = json.loads(payload)["parts"][0]["data"]
        assert len(data["questions"]) == 4
