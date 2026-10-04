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

OVERSIZE_ANSWER = "y" * (200 * 1024)
MARKER_SERIALIZED_CAP_BYTES = 96 * 1024
UUID_WIDTH = 36
MAX_ID_WIDTH = len(str(2**63 - 1))
TIMESTAMP_WIDTH = len("9999-12-31T23:59:59Z")


def _stored_marker(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    for (payload,) in rows:
        for part in json.loads(payload).get("parts", []):
            data = part.get("data") or {}
            if data.get("type") == "question_answer" and "resolved_event_id" in data:
                return payload, data
    raise AssertionError(f"no marker among {len(rows)} events of {session_id}")


def _status(url, event_id):
    return httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=10).json()["status"]


def _answer(url, session_id, event_id, text="an answer", headers=None):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": [
                {"text": text},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ]
        },
        headers=headers or {},
        timeout=15,
    )


def _dismiss(url, event_id, headers=None):
    return httpx.post(
        f"{url}/api/v1/escalate/{event_id}/dismiss",
        headers=headers or {},
        timeout=15,
    )


def _problem(resp, status, code):
    assert resp.status_code == status, (status, resp.status_code, resp.text)
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.text
    )
    body = resp.json()
    assert body["code"] == code, (code, resp.text)
    assert body["status"] == status
    assert body["title"]
    return body


def _terminate(db_url, exec_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
        )
        conn.commit()


def _decision(ctx, agent_name, question="Which?"):
    agent_id = seed_test_agent(ctx["db_url"], name=agent_name)
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
    decision = rest_escalate_ok(
        ctx["url"], session_id, {"questions": [{"question": question}]}
    )
    return exec_id, session_id, decision["event_id"]


@DUAL_BACKEND
def test_oversize_answer_is_answer_too_large(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-1")
        resp = _answer(ctx["url"], session_id, event_id, OVERSIZE_ANSWER)
        _problem(resp, 400, "decision.answer_too_large")


@DUAL_BACKEND
def test_unknown_decision_is_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, _event_id = _decision(ctx, "codes-2")
        _problem(_answer(ctx["url"], session_id, "999999"), 404, "decision.not_found")
        _problem(_answer(ctx["url"], session_id, "1"), 404, "decision.not_found")
        _problem(_dismiss(ctx["url"], "999999"), 404, "decision.not_found")


@DUAL_BACKEND
def test_answer_from_another_session_is_wrong_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, _session, event_id = _decision(ctx, "codes-3")
        agent_id = seed_test_agent(ctx["db_url"], name="codes-3-other")
        _other_exec, other_session = create_execution_via_api(
            ctx["url"], agent_id, "other"
        )
        resp = _answer(ctx["url"], other_session, event_id)
        _problem(resp, 409, "decision.wrong_session")


@DUAL_BACKEND
def test_agent_session_bearer_is_forbidden(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-4")
        headers = {"Authorization": f"Bearer {session_id}"}
        _problem(
            _answer(ctx["url"], session_id, event_id, headers=headers),
            403,
            "auth.forbidden",
        )
        _problem(_dismiss(ctx["url"], event_id, headers=headers), 403, "auth.forbidden")


@DUAL_BACKEND
def test_already_resolved_carries_the_winning_resolution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-5")
        assert _answer(ctx["url"], session_id, event_id, "first").status_code == 200

        body = _problem(
            _answer(ctx["url"], session_id, event_id, "second"),
            409,
            "decision.already_resolved",
        )
        assert body["resolution"]["kind"] == "answered"
        assert body["resolution"]["answer_text"] == "first"
        assert body["resolution"]["resolved_at"].endswith("Z")
        assert "surface" not in body["resolution"]
        assert "retry" not in body
        assert "type" not in body


@DUAL_BACKEND
def test_retried_identical_answer_reports_its_own_submission(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-retry")
        submitted = "PostgreSQL"
        assert _answer(ctx["url"], session_id, event_id, submitted).status_code == 200

        body = _problem(
            _answer(ctx["url"], session_id, event_id, submitted),
            409,
            "decision.already_resolved",
        )
        assert body["resolution"]["answer_text"] == submitted


@DUAL_BACKEND
def test_answer_into_a_terminated_execution_is_expired(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, event_id = _decision(ctx, "codes-6")
        _terminate(test_database, exec_id)
        _problem(_answer(ctx["url"], session_id, event_id), 409, "decision.expired")


@DUAL_BACKEND
def test_plain_message_into_a_terminated_execution_is_execution_terminated(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, _event_id = _decision(ctx, "codes-7")
        _terminate(test_database, exec_id)
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "hello"}]},
            timeout=15,
        )
        _problem(resp, 409, "execution.terminated")


@DUAL_BACKEND
def test_resolved_decision_under_a_terminated_execution_is_already_resolved(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, event_id = _decision(ctx, "codes-5-6")
        assert _answer(ctx["url"], session_id, event_id, "landed").status_code == 200
        _terminate(test_database, exec_id)

        body = _problem(
            _answer(ctx["url"], session_id, event_id, "later"),
            409,
            "decision.already_resolved",
        )
        assert body["resolution"]["kind"] == "answered"

        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{event_id}", timeout=10
        ).json()
        assert detail["status"] == "answered", "the feed agrees with the write path"


@DUAL_BACKEND
def test_terminal_session_under_a_live_execution_returns_the_generic_conflict(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, _event_id = _decision(ctx, "codes-8")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'completed' "
                "WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "hello"}]},
            timeout=15,
        )
        assert resp.status_code == 409
        assert not resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert set(body) == {"error"}
        assert body["error"] == "session or execution cannot accept messages", body


@DUAL_BACKEND
def test_a_valid_answer_is_recorded_and_reports_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-9")
        resp = _answer(ctx["url"], session_id, event_id, "fine")
        assert resp.status_code == 200
        assert resp.json()["session_status"] == "working"

        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{event_id}", timeout=10
        ).json()
        assert detail["status"] == "answered"
        assert detail["answer"] == "fine"


@DUAL_BACKEND
def test_dismiss_parity(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _session_id, event_id = _decision(ctx, "codes-dismiss")
        assert _dismiss(ctx["url"], event_id).status_code == 200

        body = _problem(
            _dismiss(ctx["url"], event_id), 409, "decision.already_resolved"
        )
        assert body["resolution"]["kind"] == "dismissed"

        exec2, _session2, event2 = _decision(ctx, "codes-dismiss-2")
        _terminate(test_database, exec2)
        _problem(_dismiss(ctx["url"], event2), 409, "decision.expired")


@DUAL_BACKEND
def test_repeat_dismiss_carries_resolved_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, _session_id, event_id = _decision(ctx, "codes-dismiss-at")
        assert _dismiss(ctx["url"], event_id).status_code == 200

        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{event_id}", timeout=10
        ).json()
        assert detail["status"] == "dismissed"

        body = _problem(
            _dismiss(ctx["url"], event_id), 409, "decision.already_resolved"
        )
        resolution = body["resolution"]
        assert resolution["kind"] == "dismissed"
        assert resolution["resolved_at"] == detail["dismissed_at"]
        assert "answer_text" not in resolution
        assert set(resolution) == {"kind", "resolved_at"}


@DUAL_BACKEND
def test_answer_size_gate_reserves_the_unknown_batch_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-batch-gate")

        probe = "p" * 1024
        assert _answer(ctx["url"], session_id, event_id, probe).status_code == 200
        stored, marker = _stored_marker(test_database, session_id)
        fixed_real = len(stored) - len(probe)
        fixed_reserved = (
            fixed_real
            + (UUID_WIDTH - len(marker["batch_id"]))
            + (MAX_ID_WIDTH - len(str(marker["escalation_event_id"])))
            + (MAX_ID_WIDTH - len(str(marker["resolved_event_id"])))
            + (TIMESTAMP_WIDTH - len(marker["resolved_at"]))
        )
        largest = MARKER_SERIALIZED_CAP_BYTES - fixed_reserved

        _exec2, session2, event2 = _decision(ctx, "codes-batch-gate-2")
        _problem(
            _answer(ctx["url"], session2, event2, "q" * (largest + 1)),
            400,
            "decision.answer_too_large",
        )
        assert _status(ctx["url"], event2) == "pending"

        assert _answer(ctx["url"], session2, event2, "r" * largest).status_code == 200
        landed, _marker = _stored_marker(test_database, session2)
        assert len(landed) <= MARKER_SERIALIZED_CAP_BYTES
        assert _status(ctx["url"], event2) == "answered"


@DUAL_BACKEND
def test_an_unreachable_session_lookup_answers_500_and_never_success(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec, session_id, event_id = _decision(ctx, "codes-failclosed")
        headers = {"Authorization": f"Bearer {session_id}"}

        _problem(_dismiss(ctx["url"], event_id, headers=headers), 403, "auth.forbidden")

        with db_conn(test_database) as conn:
            conn.execute("ALTER TABLE sessions RENAME TO sessions_unreachable")
            conn.commit()
        try:
            resp = _dismiss(ctx["url"], event_id, headers=headers)
        finally:
            with db_conn(test_database) as conn:
                conn.execute("ALTER TABLE sessions_unreachable RENAME TO sessions")
                conn.commit()

        assert resp.status_code == 500, resp.text
        assert not resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert set(body) == {"error"}
        assert "sessions" in body["error"], body
