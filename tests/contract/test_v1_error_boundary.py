# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


def _error_body(resp, status, error):
    assert resp.status_code == status, resp.text
    assert not resp.headers["content-type"].startswith("application/problem+json"), (
        resp.text
    )
    assert resp.json() == {"error": error}, resp.text


@DUAL_BACKEND
def test_a_v1_route_failing_through_scheduler_error_keeps_the_error_body(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        _error_body(
            httpx.get(f"{ctx['url']}/api/v1/executions/nope", timeout=10),
            404,
            "execution not found: nope",
        )

        _error_body(
            httpx.post(
                f"{ctx['url']}/api/v1/config",
                json={"name": "", "value": "x"},
                timeout=10,
            ),
            400,
            "Config name cannot be empty",
        )

        agent_id = seed_test_agent(ctx["db_url"], name="boundary-conflict")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'completed' "
                "WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        conflict = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "hi"}]},
            timeout=10,
        )
        _error_body(conflict, 409, "session or execution cannot accept messages")


@DUAL_BACKEND
def test_unknown_session_returns_the_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _error_body(
            httpx.get(f"{ctx['url']}/api/v1/sessions/nope", timeout=10),
            404,
            "session not found: nope",
        )


@DUAL_BACKEND
def test_the_protocol_endpoints_keep_their_own_error_formats(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        rpc = httpx.post(
            f"{ctx['url']}/rpc",
            json={"jsonrpc": "2.0", "id": 7, "method": "nope"},
            timeout=10,
        )
        assert rpc.status_code == 200
        assert not rpc.headers["content-type"].startswith("application/problem+json")
        assert rpc.json() == {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": "Method not found: nope"},
            "id": 7,
        }

        worker = httpx.post(f"{ctx['url']}/api/worker/events", json={}, timeout=10)
        assert worker.status_code == 422
        assert worker.headers["content-type"].startswith("text/plain")
        assert "missing field `workerId`" in worker.text

        mcp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "nope"},
            headers={"Accept": "application/json, text/event-stream"},
            timeout=10,
        )
        assert mcp.status_code == 401
        assert mcp.headers["content-type"].startswith("application/json")
        assert not mcp.headers["content-type"].startswith("application/problem+json")
        assert mcp.json() == {
            "error": "unauthorized",
            "detail": "missing Authorization header",
        }


@DUAL_BACKEND
def test_the_wiki_surface_uses_the_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "boundary-wiki")

        missing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/nope", timeout=10
        )
        _error_body(missing, 404, "wiki page not found")

        invalid = httpx.put(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/bad",
            json={"title": "", "body": "x"},
            timeout=10,
        )
        _error_body(invalid, 400, "title must not be empty")


@DUAL_BACKEND
def test_auth_rejections_keep_the_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/escalate",
            json={"questions": [{"question": "Q?"}]},
            headers={"Authorization": "Bearer not-a-session"},
            timeout=10,
        )
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"
        assert not resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json() == {"error": "unauthorized", "detail": "session not found"}


@DUAL_BACKEND
def test_the_api_root_is_a_problem_not_the_app(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for path in ("/api", "/api/", "/api/nope"):
            resp = httpx.get(f"{ctx['url']}{path}", timeout=10)
            assert resp.status_code == 404, path
            assert resp.headers["content-type"].startswith(
                "application/problem+json"
            ), path
            assert resp.json()["code"] == "resource.not_found", path
