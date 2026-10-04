# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _detail(url, event_id):
    return httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=30)


def _answer(url, session_id, event_id, text):
    resp = httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": [
                {"text": text},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ]
        },
        timeout=15,
    )
    assert resp.status_code == 200, resp.text


@DUAL_BACKEND
def test_detail_reports_every_true_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="detail-statuses")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        pending = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Pending?"}]}
        )
        answered = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Answered?"}]}
        )
        _answer(ctx["url"], session_id, answered["event_id"], "the answer")
        dismissed = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Dismissed?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{dismissed['event_id']}/dismiss", timeout=15
        )

        assert _detail(ctx["url"], pending["event_id"]).json()["status"] == "pending"

        answered_body = _detail(ctx["url"], answered["event_id"]).json()
        assert answered_body["status"] == "answered"
        assert answered_body["answer"] == "the answer"
        assert answered_body["answered_at"].endswith("Z")
        assert answered_body["dismissed_at"] is None

        dismissed_body = _detail(ctx["url"], dismissed["event_id"]).json()
        assert dismissed_body["status"] == "dismissed"
        assert dismissed_body["answer"] is None
        assert dismissed_body["dismissed_at"].endswith("Z")

        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
            )
            conn.commit()
        assert _detail(ctx["url"], pending["event_id"]).json()["status"] == "expired"
        assert _detail(ctx["url"], answered["event_id"]).json()["status"] == "answered"


@DUAL_BACKEND
def test_detail_carries_the_full_question_set(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="detail-brief")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {"question": "One?", "context": "c"},
                    {
                        "question": "Two?",
                        "options": [
                            {"label": "A", "description": "a"},
                            {"label": "B", "description": "b"},
                        ],
                    },
                ]
            },
        )

        body = _detail(ctx["url"], decision["event_id"]).json()
        assert body["event_id"] == decision["event_id"]
        assert body["batch_id"] == decision["batch_id"]
        assert [q["question"] for q in body["questions"]] == ["One?", "Two?"]
        assert [q["index"] for q in body["questions"]] == [0, 1]
        assert body["questions"][1]["options"][1]["label"] == "B"
        assert "agent_name" not in body
        assert "hierarchical_name" not in body


@DUAL_BACKEND
def test_an_unknown_or_non_escalation_id_is_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="detail-not-found")
        create_execution_via_api(ctx["url"], agent_id, "task")

        for event_id in ("999999", "1", "not-a-number"):
            resp = _detail(ctx["url"], event_id)
            assert resp.status_code == 404, event_id
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["code"] == "decision.not_found"


@DUAL_BACKEND
def test_a_crafted_marker_naming_another_escalation_does_not_attach(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="detail-crafted")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Target?"}]}
        )
        other = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Other?"}]}
        )

        payload = json.dumps(
            {
                "parts": [
                    {
                        "data": {
                            "type": "question_answer",
                            "batch_id": target["batch_id"],
                            "escalation_event_id": int(other["event_id"]),
                            "resolved_event_id": 999001,
                            "resolved_at": "2025-01-01T00:00:00Z",
                            "answer_text": "misdirected",
                        }
                    }
                ]
            }
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()

        assert _detail(ctx["url"], target["event_id"]).json()["status"] == "pending"
        assert _detail(ctx["url"], other["event_id"]).json()["status"] == "answered"


@DUAL_BACKEND
def test_the_association_read_is_scoped_and_starts_after_the_escalation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="detail-scope")
        exec_a, session_a = create_execution_via_api(ctx["url"], agent_id, "A")
        exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "B")

        decision = rest_escalate_ok(
            ctx["url"], session_a, {"questions": [{"question": "Scoped?"}]}
        )

        payload = json.dumps(
            {
                "parts": [
                    {
                        "data": {
                            "type": "question_answer",
                            "batch_id": decision["batch_id"],
                            "escalation_event_id": int(decision["event_id"]),
                            "resolved_event_id": 999002,
                            "resolved_at": "2025-01-01T00:00:00Z",
                            "answer_text": "foreign",
                        }
                    }
                ]
            }
        )
        noise = json.dumps({"parts": [{"data": {"type": "turn_complete"}}]})
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_b, session_b, payload),
            )
            values = ", ".join(["(?, ?, 'platform', ?)"] * 500)
            params = []
            for _ in range(500):
                params.extend([exec_b, session_b, noise])
            for _ in range(4):
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) "
                    f"VALUES {values}",
                    tuple(params),
                )
            conn.commit()

        started = time.perf_counter()
        body = _detail(ctx["url"], decision["event_id"]).json()
        elapsed = time.perf_counter() - started

        assert body["status"] == "pending", (
            "a marker in another execution never attaches"
        )
        assert elapsed < 2.0, f"detail read took {elapsed:.2f}s"
