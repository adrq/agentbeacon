# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)

UNVERSIONED = [
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("GET", "/api/docs"),
    ("GET", "/api/versions"),
    ("GET", "/.well-known/agent-card.json"),
]


@DUAL_BACKEND
def test_unversioned_paths_resolve_where_they_always_did(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for method, path in UNVERSIONED:
            resp = httpx.request(method, f"{ctx['url']}{path}", timeout=5)
            assert resp.status_code == 200, (path, resp.status_code, resp.text)


@DUAL_BACKEND
def test_protocol_endpoints_are_not_under_v1(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for path in (
            "/api/v1/health",
            "/api/v1/ready",
            "/api/v1/docs",
            "/api/v1/versions",
            "/api/v1/rpc",
            "/api/v1/mcp",
            "/api/v1/worker/sync",
            "/api/v1/.well-known/agent-card.json",
        ):
            resp = httpx.get(f"{ctx['url']}{path}", timeout=5)
            assert resp.status_code == 404, (path, resp.status_code)
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["code"] == "resource.not_found"


@DUAL_BACKEND
def test_worker_transport_stays_where_it_is(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "probe"}, timeout=5
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/json")

        resp = httpx.post(f"{ctx['url']}/api/worker/events", json={}, timeout=5)
        assert resp.status_code == 422


@DUAL_BACKEND
def test_rpc_endpoint_serves_its_own_error_format(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/rpc",
            json={"jsonrpc": "2.0", "id": 1, "method": "nonexistent/method"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert not resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json() == {
            "jsonrpc": "2.0",
            "error": {
                "code": -32601,
                "message": "Method not found: nonexistent/method",
            },
            "id": 1,
        }


@DUAL_BACKEND
def test_boot_smoke(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="router-smoke")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "smoke")

        listed = httpx.get(f"{ctx['url']}/api/v1/executions", timeout=5)
        assert listed.status_code == 200

        compressed = httpx.get(
            f"{ctx['url']}/api/v1/executions",
            headers={"Accept-Encoding": "gzip"},
            timeout=5,
        )
        assert compressed.status_code == 200
        assert compressed.headers.get("content-encoding") == "gzip"

        with httpx.stream(
            "GET",
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
            headers={"Accept-Encoding": "gzip"},
            timeout=5,
        ) as stream:
            assert stream.status_code == 200
            assert "content-encoding" not in stream.headers
            assert stream.headers["content-type"].startswith("text/event-stream")
            first = next(stream.iter_lines())
            assert first.startswith("event:") or first.startswith("data:")

        unknown_v1 = httpx.get(f"{ctx['url']}/api/v1/no-such-route", timeout=5)
        assert unknown_v1.status_code == 404
        assert unknown_v1.headers["content-type"].startswith("application/problem+json")

        root = httpx.get(f"{ctx['url']}/", timeout=5)
        assert root.status_code == 200
        assert root.headers["content-type"].startswith("text/html")
        unknown_asset = httpx.get(f"{ctx['url']}/no-such-page", timeout=5)
        assert not unknown_asset.headers.get("content-type", "").startswith(
            "application/problem+json"
        )


@DUAL_BACKEND
def test_unknown_api_path_is_a_problem_not_spa_html(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/no-such-thing", timeout=5)
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "resource.not_found"


@DUAL_BACKEND
def test_method_not_allowed_under_v1(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.request("DELETE", f"{ctx['url']}/api/v1/decisions", timeout=5)
        assert resp.status_code == 405
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "request.method_not_allowed"
