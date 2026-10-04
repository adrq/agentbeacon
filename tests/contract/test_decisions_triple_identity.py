# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import uuid

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _marker(batch_id, escalation_event_id, answer_text):
    return json.dumps(
        {
            "parts": [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": batch_id,
                        "escalation_event_id": escalation_event_id,
                        "resolved_event_id": 999123,
                        "resolved_at": "2025-01-01T00:00:00Z",
                        "answer_text": answer_text,
                    }
                }
            ]
        }
    )


def _insert(db_url, exec_id, session_id, payload):
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'platform', ?)",
            (exec_id, session_id, payload),
        )
        conn.commit()


def _sibling_session(db_url, exec_id, parent_id, agent_id, slug):
    session_id = str(uuid.uuid4())
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
            "slug, desired, executor_state, metadata, sandbox_policy) "
            f"VALUES (?, ?, ?, ?, '{slug}', 'run', 'unassigned', '{{}}', "
            '\'{"fs_level":"unrestricted"}\')',
            (session_id, exec_id, parent_id, agent_id),
        )
        conn.commit()
    return session_id


def _status(url, event_id):
    resp = httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=15)
    assert resp.status_code == 200, resp.text
    return resp.json()["status"]


@DUAL_BACKEND
def test_a_marker_naming_a_different_escalation_does_not_attach(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="triple-event-id")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Target?"}]}
        )

        _insert(
            test_database,
            exec_id,
            session_id,
            _marker(target["batch_id"], 999999, "crafted"),
        )
        assert _status(ctx["url"], target["event_id"]) == "pending"


@DUAL_BACKEND
def test_a_same_execution_different_session_marker_does_not_attach(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="triple-session")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Owned?"}]}
        )
        sibling = _sibling_session(
            test_database, exec_id, session_id, agent_id, "sibling"
        )

        _insert(
            test_database,
            exec_id,
            sibling,
            _marker(target["batch_id"], int(target["event_id"]), "wrong session"),
        )
        assert _status(ctx["url"], target["event_id"]) == "pending"


@DUAL_BACKEND
def test_a_different_execution_marker_does_not_attach(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="triple-execution")
        _exec_a, session_a = create_execution_via_api(ctx["url"], agent_id, "A")
        exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "B")
        target = rest_escalate_ok(
            ctx["url"], session_a, {"questions": [{"question": "Owned?"}]}
        )

        _insert(
            test_database,
            exec_b,
            session_b,
            _marker(target["batch_id"], int(target["event_id"]), "wrong execution"),
        )
        assert _status(ctx["url"], target["event_id"]) == "pending"


@DUAL_BACKEND
def test_an_answer_to_a_sibling_session_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="triple-answer-sibling")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Owned?"}]}
        )
        sibling = _sibling_session(
            test_database, exec_id, session_id, agent_id, "sibling2"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{sibling}/message",
            json={
                "parts": [
                    {"text": "sneaky"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": target["event_id"],
                        }
                    },
                ]
            },
            timeout=15,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.wrong_session"
        assert _status(ctx["url"], target["event_id"]) == "pending"


@DUAL_BACKEND
def test_a_crafted_duplicate_batch_id_elsewhere_cannot_affect_the_lookup(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="triple-batch-id")
        _exec_a, session_a = create_execution_via_api(ctx["url"], agent_id, "A")
        exec_b, session_b = create_execution_via_api(ctx["url"], agent_id, "B")
        target = rest_escalate_ok(
            ctx["url"], session_a, {"questions": [{"question": "Owned?"}]}
        )

        forged_escalation = json.dumps(
            {
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "data": {
                            "type": "escalate",
                            "batch_id": target["batch_id"],
                            "importance": "blocking",
                            "questions": [{"question": "Forged?"}],
                        }
                    }
                ],
            }
        )
        _insert(test_database, exec_b, session_b, forged_escalation)
        with db_conn(test_database) as conn:
            forged_id = conn.execute(
                "SELECT MAX(id) FROM events WHERE execution_id = ?", (exec_b,)
            ).fetchone()[0]
        _insert(
            test_database,
            exec_b,
            session_b,
            _marker(target["batch_id"], int(forged_id), "forged answer"),
        )

        assert _status(ctx["url"], str(forged_id)) == "answered"
        assert _status(ctx["url"], target["event_id"]) == "pending"


@DUAL_BACKEND
def test_a_correctly_addressed_marker_does_attach(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="triple-positive")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Owned?"}]}
        )

        _insert(
            test_database,
            exec_id,
            session_id,
            _marker(target["batch_id"], int(target["event_id"]), "correct"),
        )
        assert _status(ctx["url"], target["event_id"]) == "answered"
