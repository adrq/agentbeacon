# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok

REGISTRY = {
    "request.invalid": 400,
    "resource.not_found": 404,
    "resource.conflict": 409,
    "auth.unauthorized": 401,
    "auth.forbidden": 403,
    "request.too_large": 413,
    "request.method_not_allowed": 405,
    "internal.error": 500,
    "wiki.revision_conflict": 409,
    "wiki.slug_conflict": 409,
    "wiki.confirmation_required": 409,
    "decision.already_resolved": 409,
    "decision.expired": 409,
    "decision.not_found": 404,
    "decision.wrong_session": 409,
    "decision.answer_too_large": 400,
    "execution.terminated": 409,
}


def _problem(resp):
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.text
    )
    body = resp.json()
    assert isinstance(body["title"], str) and body["title"]
    assert body["status"] == resp.status_code
    assert body["code"] in REGISTRY, body["code"]
    assert REGISTRY[body["code"]] == resp.status_code
    assert "retry" not in body
    assert "type" not in body
    return body


@DUAL_BACKEND
def test_the_api_fallback_is_a_problem(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/nope", timeout=5)
        assert resp.status_code == 404
        assert _problem(resp)["code"] == "resource.not_found"


@DUAL_BACKEND
def test_the_v1_fallbacks_are_problems(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        unknown = httpx.get(f"{ctx['url']}/api/v1/nope", timeout=5)
        assert unknown.status_code == 404
        assert _problem(unknown)["code"] == "resource.not_found"

        wrong_method = httpx.request(
            "PATCH", f"{ctx['url']}/api/v1/decisions", timeout=5
        )
        assert wrong_method.status_code == 405
        assert _problem(wrong_method)["code"] == "request.method_not_allowed"


@DUAL_BACKEND
def test_the_decision_emitters(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="problem-decisions")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Q?"}]}
        )

        def answer(text, event_id=decision["event_id"], headers=None):
            return httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={
                    "parts": [
                        {"text": text},
                        {
                            "data": {
                                "type": "question_answer",
                                "escalation_event_id": event_id,
                            }
                        },
                    ]
                },
                headers=headers or {},
                timeout=15,
            )

        assert _problem(answer("y" * (200 * 1024)))["code"] == (
            "decision.answer_too_large"
        )
        assert _problem(answer("x", event_id="999999"))["code"] == "decision.not_found"
        assert (
            _problem(answer("x", headers={"Authorization": f"Bearer {session_id}"}))[
                "code"
            ]
            == "auth.forbidden"
        )

        assert answer("landed").status_code == 200
        resolved = _problem(answer("again"))
        assert resolved["code"] == "decision.already_resolved"
        assert set(resolved["resolution"]) == {"kind", "answer_text", "resolved_at"}

        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
            )
            conn.commit()
        plain = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "hello"}]},
            timeout=15,
        )
        assert _problem(plain)["code"] == "execution.terminated"


@DUAL_BACKEND
def test_the_decisions_feed_emitters(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        missing = httpx.get(f"{ctx['url']}/api/v1/decisions/999999", timeout=10)
        assert _problem(missing)["code"] == "decision.not_found"

        bad_cursor = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"before": "5"}, timeout=10
        )
        assert _problem(bad_cursor)["code"] == "request.invalid"


@DUAL_BACKEND
def test_the_event_read_emitters(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="problem-events")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        invalid = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events",
            params={"limit": 5000},
            timeout=10,
        )
        assert _problem(invalid)["code"] == "request.invalid"

        missing = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/999999", timeout=10
        )
        assert _problem(missing)["code"] == "resource.not_found"


@DUAL_BACKEND
def test_framework_rejections_keep_the_axum_default(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="problem-framework")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        assert resp.status_code == 400
        assert not resp.headers["content-type"].startswith(
            "application/problem+json"
        ), "framework rejections are not normalized"


@DUAL_BACKEND
def test_no_problem_carries_a_request_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        body = _problem(httpx.get(f"{ctx['url']}/api/v1/nope", timeout=5))
        assert "request_id" not in body
