# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

BACKENDS = ["sqlite", "postgres"]


def _escalate(url, session_id, q="Q?"):
    resp = httpx.post(
        f"{url}/api/v1/escalate",
        json={"questions": [{"question": q}], "importance": "blocking"},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["event_id"]


def _baseline(db_url, exec_id, session_id):
    with db_conn(db_url) as conn:
        events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE execution_id = ?", (exec_id,)
        ).fetchone()[0]
        queue = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        desired = conn.execute(
            "SELECT desired FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
    return events, queue, desired


def _assert_unchanged(db_url, exec_id, session_id, baseline):
    assert _baseline(db_url, exec_id, session_id) == baseline, "unexpected side effect"


def _answer(url, session_id, parts, headers=None):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={"parts": parts},
        headers=headers or {},
        timeout=30,
    )


def _qa(event_id):
    return {"data": {"type": "question_answer", "escalation_event_id": event_id}}


def _problem(resp, code):
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.text
    )
    body = resp.json()
    assert body["code"] == code, (code, resp.text)
    assert body["status"] == resp.status_code
    return body


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_error_contract_zero_side_effects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(url, agent_id)
        b1 = _escalate(url, session_id, "One?")
        b2 = _escalate(url, session_id, "Two?")

        def check(make, status, code=None, fragment=None):
            base = _baseline(test_database, exec_id, session_id)
            resp = make()
            assert resp.status_code == status, (status, resp.status_code, resp.text)
            if code is not None:
                _problem(resp, code)
            if fragment is not None:
                assert fragment in resp.json()["error"], (fragment, resp.text)
            _assert_unchanged(test_database, exec_id, session_id, base)

        check(
            lambda: _answer(url, session_id, [{"text": "x"}, _qa(b1), _qa(b2)]),
            400,
            fragment="message must contain at most one question_answer part",
        )
        check(
            lambda: _answer(
                url,
                session_id,
                [
                    {"text": "x"},
                    {"data": {"type": "question_answer", "escalation_event_id": 5}},
                ],
            ),
            400,
            fragment="question_answer part requires a string escalation_event_id",
        )
        check(
            lambda: _answer(
                url,
                session_id,
                [{"text": "x"}, _qa(b1), {"data": {"type": "sender", "name": "p"}}],
            ),
            400,
            fragment="question_answer cannot carry a sender part",
        )
        check(
            lambda: _answer(url, session_id, [{"text": "y" * (200 * 1024)}, _qa(b1)]),
            400,
            code="decision.answer_too_large",
        )
        check(
            lambda: _answer(url, session_id, [{"text": "x"}, _qa("999999")]),
            404,
            code="decision.not_found",
        )
        check(
            lambda: httpx.post(f"{url}/api/v1/escalate/999999/dismiss", timeout=10),
            404,
            code="decision.not_found",
        )
        check(
            lambda: _answer(
                url,
                session_id,
                [{"text": "x"}, _qa(b1)],
                headers={"Authorization": f"Bearer {session_id}"},
            ),
            403,
            code="auth.forbidden",
        )
        check(
            lambda: httpx.post(
                f"{url}/api/v1/escalate/{b1}/dismiss",
                headers={"Authorization": f"Bearer {session_id}"},
                timeout=10,
            ),
            403,
            code="auth.forbidden",
        )
        check(
            lambda: _answer(
                url, session_id, [{"text": "z" * (2 * 1024 * 1024 + 1024)}]
            ),
            413,
        )

        data = httpx.get(
            f"{url}/api/v1/decisions", params={"execution_id": exec_id}, timeout=10
        ).json()["decisions"]
        status = {d["event_id"]: d["status"] for d in data}
        assert status[b1] == "pending" and status[b2] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_error_contract_ownership_and_already_resolved(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(url, agent_id)
        _exec2, session2 = create_execution_via_api(url, agent_id)
        batch = _escalate(url, session_id, "Owned?")

        base = _baseline(test_database, exec_id, session_id)
        resp = _answer(url, session2, [{"text": "x"}, _qa(batch)])
        assert resp.status_code == 409
        _problem(resp, "decision.wrong_session")
        _assert_unchanged(test_database, exec_id, session_id, base)

        assert (
            _answer(url, session_id, [{"text": "first"}, _qa(batch)]).status_code == 200
        )
        base2 = _baseline(test_database, exec_id, session_id)
        resp = _answer(url, session_id, [{"text": "second"}, _qa(batch)])
        assert resp.status_code == 409
        body = _problem(resp, "decision.already_resolved")
        assert body["resolution"]["kind"] == "answered"
        assert body["resolution"]["answer_text"] == "first"
        assert body["resolution"]["resolved_at"]
        assert "surface" not in body["resolution"]
        _assert_unchanged(test_database, exec_id, session_id, base2)


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_error_contract_terminate_first_write_barrier(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(url, agent_id)
        batch = _escalate(url, session_id, "Late?")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
            )
            conn.commit()

        base = _baseline(test_database, exec_id, session_id)
        resp = _answer(url, session_id, [{"text": "too late"}, _qa(batch)])
        assert resp.status_code == 409
        _problem(resp, "decision.expired")
        _assert_unchanged(test_database, exec_id, session_id, base)

        base = _baseline(test_database, exec_id, session_id)
        resp = _answer(url, session_id, [{"text": "hello"}])
        assert resp.status_code == 409
        _problem(resp, "execution.terminated")
        _assert_unchanged(test_database, exec_id, session_id, base)
