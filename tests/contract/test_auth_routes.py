# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import pathlib
import re
import uuid

import httpx
import pytest

from tests.testhelpers import (
    TEST_PASSWORD,
    TEST_USERNAME,
    admit_worker_with_token,
    agent_headers,
    agent_token,
    anonymous_headers,
    assert_forbidden,
    assert_problem,
    assert_unauthorized,
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    human_headers,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)

PUBLIC = "public"
HUMAN = "human"
AGENT = "agent"
WORKER = "worker"
EXEC = "agent:execution"
SESSION = "agent:session"
SESSION_Q = "agent:session_query"
EXEC_Q = "agent:execution_query"
WIKI = "agent:wiki"
SCOPED = {EXEC, SESSION, SESSION_Q, EXEC_Q, WIKI}

V1 = "/api/v1"

ROUTES = {
    ("GET", "/api/health"): PUBLIC,
    ("GET", "/api/ready"): PUBLIC,
    ("GET", "/api/versions"): PUBLIC,
    ("GET", "/api/docs"): PUBLIC,
    ("GET", "/.well-known/agent-card.json"): PUBLIC,
    ("ANY", "/api"): PUBLIC,
    ("ANY", "/api/"): PUBLIC,
    ("ANY", "/api/{*rest}"): PUBLIC,
    ("GET", "/"): PUBLIC,
    ("GET", "/index.html"): PUBLIC,
    ("GET", "/docs"): PUBLIC,
    ("GET", "/docs/"): PUBLIC,
    ("GET", "/docs/{*path}"): PUBLIC,
    ("POST", f"{V1}/auth/login"): PUBLIC,
    ("POST", "/api/worker/sync"): WORKER,
    ("POST", "/api/worker/events"): WORKER,
    ("POST", "/api/worker/token"): WORKER,
    ("POST", "/mcp"): AGENT,
    ("GET", "/mcp"): AGENT,
    ("DELETE", "/mcp"): AGENT,
    ("POST", f"{V1}/messages"): AGENT,
    ("POST", f"{V1}/escalate"): AGENT,
    ("GET", f"{V1}/secrets/{{name}}"): AGENT,
    ("GET", f"{V1}/executions/{{id}}"): EXEC,
    ("GET", f"{V1}/executions/{{id}}/events"): EXEC,
    ("GET", f"{V1}/executions/{{execution_id}}/events/{{event_id}}"): EXEC,
    ("GET", f"{V1}/executions/{{id}}/events/stream"): EXEC,
    ("GET", f"{V1}/executions/{{id}}/agents"): EXEC,
    ("GET", f"{V1}/executions/{{id}}/sessions"): EXEC,
    ("GET", f"{V1}/executions/{{id}}/secrets"): EXEC,
    ("GET", f"{V1}/sessions/{{id}}"): SESSION,
    ("GET", f"{V1}/sessions/{{id}}/events"): SESSION,
    ("GET", f"{V1}/sessions/{{id}}/worktree"): SESSION,
    ("GET", f"{V1}/sessions/{{id}}/worktree/diff"): SESSION,
    ("GET", f"{V1}/sessions/{{id}}/worktree/branches"): SESSION,
    ("GET", f"{V1}/messages"): SESSION_Q,
    ("GET", f"{V1}/sessions"): EXEC_Q,
    ("GET", f"{V1}/wiki/search"): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/pages"): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/pages/{{slug}}"): WIKI,
    ("PUT", f"{V1}/projects/{{project_id}}/wiki/pages/{{slug}}"): WIKI,
    ("PATCH", f"{V1}/projects/{{project_id}}/wiki/pages/{{slug}}"): WIKI,
    ("DELETE", f"{V1}/projects/{{project_id}}/wiki/pages/{{slug}}"): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/pages/{{slug}}/revisions"): WIKI,
    (
        "GET",
        f"{V1}/projects/{{project_id}}/wiki/pages/{{slug}}/revisions/{{rev}}",
    ): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/tags"): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/subscriptions"): WIKI,
    ("POST", f"{V1}/projects/{{project_id}}/wiki/subscriptions"): WIKI,
    ("DELETE", f"{V1}/projects/{{project_id}}/wiki/subscriptions/{{sub_id}}"): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/changes"): WIKI,
    ("GET", f"{V1}/projects/{{project_id}}/wiki/export"): WIKI,
    ("GET", f"{V1}/executions"): HUMAN,
    ("POST", f"{V1}/executions"): HUMAN,
    ("PATCH", f"{V1}/executions/{{id}}"): HUMAN,
    ("POST", f"{V1}/executions/{{id}}/terminate"): HUMAN,
    ("POST", f"{V1}/executions/{{id}}/agents"): HUMAN,
    ("DELETE", f"{V1}/executions/{{id}}/agents/{{agent_id}}"): HUMAN,
    ("POST", f"{V1}/executions/{{id}}/secrets"): HUMAN,
    ("DELETE", f"{V1}/executions/{{id}}/secrets/{{name}}"): HUMAN,
    ("DELETE", f"{V1}/sessions/{{id}}/worktree"): HUMAN,
    ("POST", f"{V1}/sessions/{{id}}/message"): HUMAN,
    ("POST", f"{V1}/sessions/{{id}}/terminate"): HUMAN,
    ("POST", f"{V1}/sessions/{{id}}/stop"): HUMAN,
    ("POST", f"{V1}/sessions/{{id}}/continue"): HUMAN,
    ("POST", f"{V1}/sessions/{{id}}/recover"): HUMAN,
    ("GET", f"{V1}/decisions"): HUMAN,
    ("GET", f"{V1}/decisions/{{event_id}}"): HUMAN,
    ("POST", f"{V1}/escalate/{{event_id}}/dismiss"): HUMAN,
    ("GET", f"{V1}/agents"): HUMAN,
    ("POST", f"{V1}/agents"): HUMAN,
    ("GET", f"{V1}/agents/{{id}}"): HUMAN,
    ("PATCH", f"{V1}/agents/{{id}}"): HUMAN,
    ("DELETE", f"{V1}/agents/{{id}}"): HUMAN,
    ("GET", f"{V1}/config"): HUMAN,
    ("POST", f"{V1}/config"): HUMAN,
    ("GET", f"{V1}/drivers"): HUMAN,
    ("POST", f"{V1}/drivers"): HUMAN,
    ("GET", f"{V1}/drivers/{{id}}"): HUMAN,
    ("PATCH", f"{V1}/drivers/{{id}}"): HUMAN,
    ("DELETE", f"{V1}/drivers/{{id}}"): HUMAN,
    ("GET", f"{V1}/drivers/{{platform}}/descriptor"): HUMAN,
    ("GET", f"{V1}/drivers/{{platform}}/models"): HUMAN,
    ("GET", f"{V1}/mcp-servers"): HUMAN,
    ("POST", f"{V1}/mcp-servers"): HUMAN,
    ("GET", f"{V1}/mcp-servers/{{id}}"): HUMAN,
    ("PATCH", f"{V1}/mcp-servers/{{id}}"): HUMAN,
    ("DELETE", f"{V1}/mcp-servers/{{id}}"): HUMAN,
    ("GET", f"{V1}/projects"): HUMAN,
    ("POST", f"{V1}/projects"): HUMAN,
    ("GET", f"{V1}/projects/{{id}}"): HUMAN,
    ("PATCH", f"{V1}/projects/{{id}}"): HUMAN,
    ("DELETE", f"{V1}/projects/{{id}}"): HUMAN,
    ("GET", f"{V1}/projects/{{id}}/agents"): HUMAN,
    ("POST", f"{V1}/projects/{{id}}/agents"): HUMAN,
    ("DELETE", f"{V1}/projects/{{id}}/agents/{{agent_id}}"): HUMAN,
    ("GET", f"{V1}/projects/{{id}}/mcp-servers"): HUMAN,
    ("POST", f"{V1}/projects/{{id}}/mcp-servers"): HUMAN,
    ("DELETE", f"{V1}/projects/{{id}}/mcp-servers/{{mcp_server_id}}"): HUMAN,
    ("GET", f"{V1}/projects/{{id}}/secrets"): HUMAN,
    ("POST", f"{V1}/projects/{{id}}/secrets"): HUMAN,
    ("PATCH", f"{V1}/projects/{{id}}/secrets/{{name}}"): HUMAN,
    ("DELETE", f"{V1}/projects/{{id}}/secrets/{{name}}"): HUMAN,
    ("GET", f"{V1}/wiki/tags"): HUMAN,
    ("POST", f"{V1}/wiki/tags/{{tag_id}}/members"): HUMAN,
    ("PATCH", f"{V1}/wiki/tags/{{tag_id}}/members/{{project}}"): HUMAN,
    ("DELETE", f"{V1}/wiki/tags/{{tag_id}}/members/{{project}}"): HUMAN,
    ("GET", f"{V1}/auth/logins"): HUMAN,
    ("DELETE", f"{V1}/auth/logins/{{id}}"): HUMAN,
    ("POST", f"{V1}/auth/logout"): HUMAN,
    ("POST", f"{V1}/workers"): HUMAN,
    ("POST", "/rpc"): HUMAN,
}

_SRC = pathlib.Path(__file__).resolve().parents[2] / "scheduler" / "src"


def _routes_in(source, prefix):
    found = set()
    for chunk in source.split(".route(")[1:]:
        named = re.match(r'\s*"([^"]+)"\s*,', chunk)
        if not named:
            continue
        body = chunk[named.end() :].split(".route(")[0][:600]
        path = prefix + named.group(1)
        if re.match(r"\s*any\(", body):
            found.add(("ANY", path))
        for verb in re.findall(r"\b(get|post|put|patch|delete)\(", body):
            found.add((verb.upper(), path))
    return found


def _registered_routes():
    api = _SRC / "api"
    routes = set()
    v1 = (api / "v1" / "mod.rs").read_text()
    for module in set(re.findall(r"\.merge\((\w+)::routes\(\)\)", v1)):
        routes |= _routes_in((api / f"{module}.rs").read_text(), V1)
    top = (api / "mod.rs").read_text()
    routes |= _routes_in(top, "")
    for module in set(re.findall(r"\.merge\((\w+)::routes\(\)\)", top)):
        routes |= _routes_in((api / f"{module}.rs").read_text(), "")
    routes |= _routes_in((_SRC / "app" / "mod.rs").read_text(), "")
    return routes


def test_every_registered_route_is_classified():
    registered = _registered_routes()
    assert registered - set(ROUTES) == set()
    assert set(ROUTES) - registered == set()


_PARAMS = {
    "id": "00000000-0000-0000-0000-000000000001",
    "execution_id": "00000000-0000-0000-0000-000000000002",
    "event_id": "1",
    "project_id": "00000000-0000-0000-0000-000000000003",
    "slug": "nope",
    "rev": "1",
    "sub_id": "00000000-0000-0000-0000-000000000004",
    "tag_id": "00000000-0000-0000-0000-000000000005",
    "project": "nope",
    "agent_id": "00000000-0000-0000-0000-000000000006",
    "mcp_server_id": "00000000-0000-0000-0000-000000000007",
    "platform": "acp",
    "name": "NOPE",
    "*rest": "nope",
    "*path": "nope.html",
}


_ID = _PARAMS["id"]


def _code(code):
    return f'"code":"{code}"'


def _error(text):
    return json.dumps({"error": text}, separators=(",", ":"))


def _missing(field):
    return (
        "Failed to deserialize the JSON body into the target type: "
        f"missing field `{field}` at line 1 column 2"
    )


PUBLIC_RESPONSES = {
    ("ANY", "/api"): (404, _code("resource.not_found")),
    ("ANY", "/api/"): (404, _code("resource.not_found")),
    ("ANY", "/api/{*rest}"): (404, _code("resource.not_found")),
    ("GET", "/"): (200, "<!doctype html>"),
    ("GET", "/.well-known/agent-card.json"): (200, '"name":"AgentBeacon Scheduler"'),
    ("GET", "/api/docs"): (200, "# AgentBeacon REST API Reference"),
    ("GET", "/api/health"): (200, '{"status":"healthy"}'),
    ("GET", "/api/ready"): (200, '{"status":"ready"}'),
    ("GET", "/api/versions"): (200, '"rest_versions":["v1.0"]'),
    ("GET", "/docs"): (308, ""),
    ("GET", "/docs/"): (200, "<!DOCTYPE html>"),
    ("GET", "/docs/{*path}"): (404, "<!DOCTYPE html>"),
    ("GET", "/index.html"): (200, "<!doctype html>"),
    ("POST", f"{V1}/auth/login"): (400, _code("request.invalid")),
}

HUMAN_RESPONSES = {
    ("DELETE", f"{V1}/agents/{{id}}"): (404, _error(f"agent not found: {_ID}")),
    ("DELETE", f"{V1}/auth/logins/{{id}}"): (404, _code("resource.not_found")),
    ("DELETE", f"{V1}/drivers/{{id}}"): (404, _error(f"driver not found: {_ID}")),
    ("DELETE", f"{V1}/executions/{{id}}/agents/{{agent_id}}"): (
        404,
        _error(f"execution not found: {_ID}"),
    ),
    ("DELETE", f"{V1}/executions/{{id}}/secrets/{{name}}"): (
        404,
        _code("resource.not_found"),
    ),
    ("DELETE", f"{V1}/mcp-servers/{{id}}"): (
        404,
        _error(f"MCP server not found: {_ID}"),
    ),
    ("DELETE", f"{V1}/projects/{{id}}"): (404, _error(f"project not found: {_ID}")),
    ("DELETE", f"{V1}/projects/{{id}}/agents/{{agent_id}}"): (
        404,
        _error(f"project not found: {_ID}"),
    ),
    ("DELETE", f"{V1}/projects/{{id}}/mcp-servers/{{mcp_server_id}}"): (
        404,
        _error(f"project not found: {_ID}"),
    ),
    ("DELETE", f"{V1}/projects/{{id}}/secrets/{{name}}"): (
        404,
        _code("resource.not_found"),
    ),
    ("DELETE", f"{V1}/sessions/{{id}}/worktree"): (
        404,
        _error(f"session not found: {_ID}"),
    ),
    ("DELETE", f"{V1}/wiki/tags/{{tag_id}}/members/{{project}}"): (
        404,
        _error("tag_not_found"),
    ),
    ("GET", f"{V1}/agents"): (200, '"name":"routes-agent"'),
    ("GET", f"{V1}/agents/{{id}}"): (404, _error(f"agent not found: {_ID}")),
    ("GET", f"{V1}/auth/logins"): (200, '"current":true'),
    ("GET", f"{V1}/config"): (200, '"name":"briefing.coordination"'),
    ("GET", f"{V1}/decisions"): (200, '{"decisions":[]}'),
    ("GET", f"{V1}/decisions/{{event_id}}"): (404, _code("decision.not_found")),
    ("GET", f"{V1}/drivers"): (200, '"platform":"acp"'),
    ("GET", f"{V1}/drivers/{{id}}"): (404, _error(f"driver not found: {_ID}")),
    ("GET", f"{V1}/drivers/{{platform}}/descriptor"): (200, '"fields":['),
    ("GET", f"{V1}/drivers/{{platform}}/models"): (200, "[]"),
    ("GET", f"{V1}/executions"): (200, '"context_id"'),
    ("GET", f"{V1}/mcp-servers"): (200, "[]"),
    ("GET", f"{V1}/mcp-servers/{{id}}"): (404, _error(f"MCP server not found: {_ID}")),
    ("GET", f"{V1}/projects"): (200, "[]"),
    ("GET", f"{V1}/projects/{{id}}"): (404, _error(f"project not found: {_ID}")),
    ("GET", f"{V1}/projects/{{id}}/agents"): (404, _error(f"project not found: {_ID}")),
    ("GET", f"{V1}/projects/{{id}}/mcp-servers"): (
        404,
        _error(f"project not found: {_ID}"),
    ),
    ("GET", f"{V1}/projects/{{id}}/secrets"): (404, _code("resource.not_found")),
    ("GET", f"{V1}/wiki/tags"): (200, "[]"),
    ("PATCH", f"{V1}/agents/{{id}}"): (404, _error(f"agent not found: {_ID}")),
    ("PATCH", f"{V1}/drivers/{{id}}"): (404, _error(f"driver not found: {_ID}")),
    ("PATCH", f"{V1}/executions/{{id}}"): (400, _code("request.invalid")),
    ("PATCH", f"{V1}/mcp-servers/{{id}}"): (
        404,
        _error(f"MCP server not found: {_ID}"),
    ),
    ("PATCH", f"{V1}/projects/{{id}}"): (404, _error(f"project not found: {_ID}")),
    ("PATCH", f"{V1}/projects/{{id}}/secrets/{{name}}"): (
        400,
        _code("request.invalid"),
    ),
    ("PATCH", f"{V1}/wiki/tags/{{tag_id}}/members/{{project}}"): (
        400,
        _error(_missing("access_level")),
    ),
    ("POST", f"{V1}/agents"): (422, _missing("name")),
    ("POST", f"{V1}/config"): (422, _missing("name")),
    ("POST", f"{V1}/drivers"): (422, _missing("name")),
    ("POST", f"{V1}/escalate/{{event_id}}/dismiss"): (404, _code("decision.not_found")),
    ("POST", f"{V1}/executions"): (422, _missing("root_agent_id")),
    ("POST", f"{V1}/executions/{{id}}/agents"): (422, _missing("agent_id")),
    ("POST", f"{V1}/executions/{{id}}/secrets"): (400, _code("request.invalid")),
    ("POST", f"{V1}/executions/{{id}}/terminate"): (
        404,
        _error(f"execution not found: {_ID}"),
    ),
    ("POST", f"{V1}/mcp-servers"): (422, _missing("name")),
    ("POST", f"{V1}/projects"): (422, _missing("name")),
    ("POST", f"{V1}/projects/{{id}}/agents"): (422, _missing("agent_id")),
    ("POST", f"{V1}/projects/{{id}}/mcp-servers"): (422, _missing("mcp_server_id")),
    ("POST", f"{V1}/projects/{{id}}/secrets"): (400, _code("request.invalid")),
    ("POST", f"{V1}/sessions/{{id}}/continue"): (
        404,
        _error(f"session not found: {_ID}"),
    ),
    ("POST", f"{V1}/sessions/{{id}}/message"): (422, _missing("parts")),
    ("POST", f"{V1}/sessions/{{id}}/recover"): (
        404,
        _error(f"session not found: {_ID}"),
    ),
    ("POST", f"{V1}/sessions/{{id}}/stop"): (404, _error(f"session not found: {_ID}")),
    ("POST", f"{V1}/sessions/{{id}}/terminate"): (
        404,
        _error(f"session not found: {_ID}"),
    ),
    ("POST", f"{V1}/wiki/tags/{{tag_id}}/members"): (400, _error(_missing("project"))),
    ("POST", f"{V1}/workers"): (201, '"worker_id":'),
    ("POST", "/rpc"): (200, '"code":-32700'),
}


def _concrete(path):
    return re.sub(r"\{([^}]+)\}", lambda m: _PARAMS[m.group(1)], path)


def _send(url, method, path, headers):
    if method == "ANY":
        method = "GET"
    return httpx.request(
        method, f"{url}{_concrete(path)}", headers=headers, timeout=10, content=b""
    )


def _of(*kinds):
    return sorted(k for k, v in ROUTES.items() if v in kinds)


def _assert_401(resp):
    assert_unauthorized(resp)


def _assert_403(resp):
    assert_forbidden(resp)


@pytest.fixture(scope="module")
def world():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="routes-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        other_exec, other_session = create_execution_via_api(
            ctx["url"], agent_id, "other"
        )
        worker_id, worker_tok = admit_worker_with_token(ctx["url"])
        yield {
            **ctx,
            "exec_id": exec_id,
            "session_id": session_id,
            "other_exec": other_exec,
            "other_session": other_session,
            "worker_id": worker_id,
            "worker_headers": {"Authorization": f"Bearer {worker_tok}"},
        }


@pytest.mark.parametrize("route", _of(HUMAN, AGENT, WORKER, *SCOPED))
def test_every_non_public_route_refuses_anonymous(world, route):
    _assert_401(_send(world["url"], *route, anonymous_headers()))


def test_response_tables_cover_their_routes():
    assert sorted(PUBLIC_RESPONSES) == _of(PUBLIC)
    assert sorted(HUMAN_RESPONSES) == [
        r for r in _of(HUMAN) if r != ("POST", f"{V1}/auth/logout")
    ]


@pytest.mark.parametrize("route", _of(PUBLIC))
def test_public_routes_need_no_credential(world, route):
    status, fragment = PUBLIC_RESPONSES[route]
    resp = _send(world["url"], *route, anonymous_headers())
    assert resp.status_code == status, resp.text
    assert fragment in resp.text


@pytest.mark.parametrize("route", sorted(HUMAN_RESPONSES))
def test_human_only_routes_admit_the_human(world, route):
    status, fragment = HUMAN_RESPONSES[route]
    method, path = route
    body = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
    resp = httpx.request(
        method,
        f"{world['url']}{_concrete(path)}",
        headers=human_headers(world["url"]),
        timeout=10,
        **body,
    )
    assert resp.status_code == status, resp.text
    assert fragment in resp.text


@pytest.mark.parametrize("route", _of(HUMAN))
def test_human_only_routes_refuse_agent_token(world, route):
    headers = agent_headers(world["url"], world["session_id"])
    _assert_403(_send(world["url"], *route, headers))


@pytest.mark.parametrize("route", _of(AGENT))
def test_agent_only_routes_refuse_human_token(world, route):
    _assert_403(_send(world["url"], *route, human_headers(world["url"])))


@pytest.mark.parametrize("route", _of(WORKER))
def test_worker_routes_refuse_human_and_agent_tokens(world, route):
    _assert_403(_send(world["url"], *route, human_headers(world["url"])))
    agent = agent_headers(world["url"], world["session_id"])
    _assert_403(_send(world["url"], *route, agent))


@pytest.mark.parametrize("route", _of(HUMAN, AGENT, *SCOPED))
def test_human_and_agent_routes_refuse_worker_token(world, route):
    _assert_403(_send(world["url"], *route, world["worker_headers"]))


def _agent_reads(world, exec_id, session_id):
    return [
        f"{V1}/executions/{exec_id}",
        f"{V1}/executions/{exec_id}/events",
        f"{V1}/executions/{exec_id}/agents",
        f"{V1}/executions/{exec_id}/sessions",
        f"{V1}/executions/{exec_id}/secrets",
        f"{V1}/sessions/{session_id}",
        f"{V1}/sessions/{session_id}/events",
        f"{V1}/messages?session_id={session_id}",
        f"{V1}/sessions?execution_id={exec_id}",
    ]


def test_agent_reads_own_execution(world):
    headers = agent_headers(world["url"], world["session_id"])
    for path in _agent_reads(world, world["exec_id"], world["session_id"]):
        resp = httpx.get(f"{world['url']}{path}", headers=headers, timeout=10)
        assert resp.status_code == 200, (path, resp.text)
    with db_conn(world["db_url"]) as conn:
        event_id = conn.execute(
            "SELECT MIN(id) FROM events WHERE execution_id = ?", (world["exec_id"],)
        ).fetchone()[0]
    resp = httpx.get(
        f"{world['url']}{V1}/executions/{world['exec_id']}/events/{event_id}",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, resp.text


_EXECUTION_SCOPED = (EXEC, SESSION, SESSION_Q, EXEC_Q)


def _scoped_target(world, route, target):
    if target == "foreign":
        exec_id, session_id = world["other_exec"], world["other_session"]
        with db_conn(world["db_url"]) as conn:
            event_id = conn.execute(
                "SELECT MIN(id) FROM events WHERE execution_id = ?", (exec_id,)
            ).fetchone()[0]
        assert event_id is not None
    else:
        exec_id = session_id = "00000000-0000-0000-0000-00000000dead"
        event_id = 999999999
    _, path = route
    kind = ROUTES[route]
    if kind == SESSION_Q:
        return f"{path}?session_id={session_id}"
    if kind == EXEC_Q:
        return f"{path}?execution_id={exec_id}"
    values = {
        "id": session_id if kind == SESSION else exec_id,
        "execution_id": exec_id,
        "event_id": str(event_id),
    }
    return re.sub(r"\{([^}]+)\}", lambda m: values[m.group(1)], path)


def test_execution_scope_sweep_covers_every_scoped_route():
    assert _of(*_EXECUTION_SCOPED) == sorted(
        ("GET", f"{V1}{path}")
        for path in (
            "/executions/{id}",
            "/executions/{id}/events",
            "/executions/{execution_id}/events/{event_id}",
            "/executions/{id}/events/stream",
            "/executions/{id}/agents",
            "/executions/{id}/sessions",
            "/executions/{id}/secrets",
            "/sessions/{id}",
            "/sessions/{id}/events",
            "/sessions/{id}/worktree",
            "/sessions/{id}/worktree/diff",
            "/sessions/{id}/worktree/branches",
            "/messages",
            "/sessions",
        )
    )


@pytest.mark.parametrize("target", ["foreign", "nonexistent"])
@pytest.mark.parametrize("route", _of(*_EXECUTION_SCOPED))
def test_agent_reads_other_execution_forbidden(world, route, target):
    headers = anonymous_headers(
        {"Authorization": f"Bearer {agent_token(world['url'], world['session_id'])}"}
    )
    url = f"{world['url']}{_scoped_target(world, route, target)}"
    with httpx.stream("GET", url, headers=headers, timeout=10) as resp:
        resp.read()
        _assert_403(resp)


@pytest.mark.parametrize("path", [f"{V1}/sessions", f"{V1}/messages"])
def test_agent_query_scoped_reads_need_their_parameter(world, path):
    headers = anonymous_headers(
        {"Authorization": f"Bearer {agent_token(world['url'], world['session_id'])}"}
    )
    _assert_403(httpx.get(f"{world['url']}{path}", headers=headers, timeout=10))


def test_agent_cannot_create_execution_or_patch_ceiling(world):
    headers = agent_headers(world["url"], world["session_id"])
    created = httpx.post(
        f"{world['url']}{V1}/executions",
        json={"root_agent_id": "x", "agent_ids": ["x"], "parts": [{"text": "go"}]},
        headers=headers,
        timeout=10,
    )
    _assert_403(created)
    patched = httpx.patch(
        f"{world['url']}{V1}/executions/{world['exec_id']}",
        json={"network": {"kind": "sandboxed", "mode": "none"}},
        headers=headers,
        timeout=10,
    )
    _assert_403(patched)


def test_wiki_agent_without_project_is_403(world):
    project = create_project_via_api(world["url"], "routes-wiki")
    resp = httpx.get(
        f"{world['url']}{V1}/projects/{project['id']}/wiki/pages",
        headers=agent_headers(world["url"], world["session_id"]),
        timeout=10,
    )
    _assert_403(resp)


def test_unknown_api_path_is_404_without_credentials(world):
    for path in ("/api/nope", f"{V1}/nope"):
        resp = httpx.get(
            f"{world['url']}{path}", headers=anonymous_headers(), timeout=10
        )
        assert_problem(resp, 404, "resource.not_found", "no route matches this path")


@pytest.mark.parametrize("header", ["Bearer", "Basic abc", "Token abc", "Bearertoken"])
def test_malformed_authorization_is_401(world, header):
    resp = httpx.get(
        f"{world['url']}{V1}/agents",
        headers=anonymous_headers({"Authorization": header}),
        timeout=10,
    )
    _assert_401(resp)


def test_session_id_is_not_a_credential(world):
    headers = anonymous_headers({"Authorization": f"Bearer {world['session_id']}"})
    for method, path in (
        ("POST", "/mcp"),
        ("POST", f"{V1}/messages"),
        ("GET", f"{V1}/executions/{world['exec_id']}"),
    ):
        _assert_401(
            httpx.request(method, f"{world['url']}{path}", headers=headers, timeout=10)
        )


def _cookie_login(url):
    resp = httpx.post(
        f"{url}{V1}/auth/login",
        json={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "delivery": "cookie",
        },
        headers=anonymous_headers(),
        timeout=30,
    )
    assert resp.status_code == 201, resp.text
    pair = resp.headers["set-cookie"].split(";", 1)[0]
    return pair


def _sent_headers(resp):
    return resp.request.headers


def test_authorization_header_wins_over_cookie(world):
    cookie = _cookie_login(world["url"])
    resp = httpx.get(
        f"{world['url']}{V1}/agents",
        headers={**agent_headers(world["url"], world["session_id"]), "Cookie": cookie},
        timeout=10,
    )
    _assert_403(resp)


def test_cookie_naming_a_session_token_is_401(world):
    token = agent_token(world["url"], world["session_id"])
    resp = httpx.get(
        f"{world['url']}{V1}/executions/{world['exec_id']}",
        headers=anonymous_headers(
            {"Cookie": f"agentbeacon_login_{world['port']}={token}"}
        ),
        timeout=10,
    )
    _assert_401(resp)


def _cookie_post(url, cookie, origin=None):
    headers = {"Cookie": cookie}
    if origin is not None:
        headers["Origin"] = origin
    resp = httpx.post(
        f"{url}{V1}/projects",
        json={"name": f"csrf-{uuid.uuid4().hex[:8]}", "path": "/tmp"},
        headers=anonymous_headers(headers),
        timeout=10,
    )
    assert "authorization" not in _sent_headers(resp)
    return resp


def test_cookie_write_without_origin_is_403(world):
    _assert_403(_cookie_post(world["url"], _cookie_login(world["url"])))


def test_cookie_write_from_foreign_origin_is_403(world):
    resp = _cookie_post(
        world["url"], _cookie_login(world["url"]), "http://evil.example"
    )
    _assert_403(resp)


def test_cookie_write_from_own_origin_succeeds(world):
    resp = _cookie_post(world["url"], _cookie_login(world["url"]), world["url"])
    assert resp.status_code == 201, resp.text


def test_bearer_write_ignores_a_foreign_origin(world):
    resp = httpx.post(
        f"{world['url']}{V1}/projects",
        json={"name": f"bearer-origin-{uuid.uuid4().hex[:8]}", "path": "/tmp"},
        headers={**human_headers(world["url"]), "Origin": "https://unlisted.example"},
        timeout=10,
    )
    assert resp.status_code == 201, resp.text


def test_bearer_write_needs_no_origin(world):
    resp = httpx.post(
        f"{world['url']}{V1}/projects",
        json={"name": f"bearer-{uuid.uuid4().hex[:8]}", "path": "/tmp"},
        headers=human_headers(world["url"]),
        timeout=10,
    )
    assert resp.status_code == 201, resp.text


def test_cookie_read_needs_no_origin(world):
    cookie = _cookie_login(world["url"])
    resp = httpx.get(
        f"{world['url']}{V1}/agents",
        headers=anonymous_headers({"Cookie": cookie}),
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    assert "authorization" not in _sent_headers(resp)


def _open_stream(url, exec_id, headers, frames, stop):
    with httpx.stream(
        "GET",
        f"{url}{V1}/executions/{exec_id}/events/stream",
        headers=headers,
        timeout=httpx.Timeout(30, read=60),
    ) as resp:
        frames.append(("status", resp.status_code))
        try:
            for line in resp.iter_lines():
                if stop.is_set():
                    return
                frames.append(("line", line))
        except httpx.RemoteProtocolError:
            pass
        frames.append(("closed", None))


def test_sse_stream_authenticates_by_cookie(world):
    cookie = _cookie_login(world["url"])
    with httpx.stream(
        "GET",
        f"{world['url']}{V1}/executions/{world['exec_id']}/events/stream",
        headers=anonymous_headers({"Cookie": cookie}),
        timeout=10,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


def test_sse_stream_refuses_anonymous(world):
    resp = httpx.get(
        f"{world['url']}{V1}/executions/{world['exec_id']}/events/stream",
        headers=anonymous_headers(),
        timeout=10,
    )
    _assert_401(resp)


def _message_lines(frames, marker):
    return [f for f in frames if f[0] == "line" and marker in (f[1] or "")]


def _post_user_message(url, session_id, text):
    resp = httpx.post(
        f"{url}{V1}/sessions/{session_id}/message",
        json={"parts": [{"text": text}]},
        headers=human_headers(url),
        timeout=10,
    )
    assert resp.status_code == 200, resp.text


def _revoke_by_delete(ctx, cookie):
    token = cookie.split("=", 1)[1]
    resp = httpx.delete(
        f"{ctx['url']}{V1}/auth/logins/{hashlib.sha256(token.encode()).hexdigest()}",
        headers=human_headers(ctx["url"]),
        timeout=10,
    )
    assert resp.status_code == 204


def _assert_problem(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == code
    if status == 401:
        assert resp.headers["www-authenticate"] == "Bearer"


def test_handler_auth_failures_are_problem_documents(world):
    result = mcp_tools_call(
        world["url"],
        world["session_id"],
        "delegate",
        {"agent": "routes-agent", "prompt": "x"},
    )
    child = json.loads(result["content"][0]["text"])["session_id"]
    escalated = httpx.post(
        f"{world['url']}{V1}/escalate",
        json={"questions": [{"question": "Q?"}]},
        headers=agent_headers(world["url"], child),
        timeout=10,
    )
    _assert_problem(escalated, 403, "auth.forbidden")

    foreign = httpx.post(
        f"{world['url']}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers=agent_headers(
            world["url"],
            world["session_id"],
            {
                "Origin": "http://evil.example",
                "Accept": "application/json, text/event-stream",
            },
        ),
        timeout=10,
    )
    _assert_problem(foreign, 403, "auth.forbidden")


def test_auth_failures_are_problem_documents_on_every_surface(world):
    project = create_project_via_api(world["url"], "surface-wiki")
    anonymous = anonymous_headers()
    for method, path in (
        ("GET", f"{V1}/agents"),
        ("GET", f"{V1}/projects/{project['id']}/wiki/pages"),
        ("POST", "/mcp"),
        ("POST", "/api/worker/sync"),
        ("POST", "/rpc"),
    ):
        _assert_problem(
            httpx.request(
                method, f"{world['url']}{path}", headers=anonymous, timeout=10
            ),
            401,
            "auth.unauthorized",
        )
    agent = agent_headers(world["url"], world["session_id"])
    for method, path in (
        ("GET", f"{V1}/agents"),
        ("GET", f"{V1}/wiki/tags"),
        ("POST", "/api/worker/sync"),
        ("POST", "/rpc"),
    ):
        _assert_problem(
            httpx.request(method, f"{world['url']}{path}", headers=agent, timeout=10),
            403,
            "auth.forbidden",
        )
    _assert_problem(
        httpx.post(
            f"{world['url']}/mcp", headers=human_headers(world["url"]), timeout=10
        ),
        403,
        "auth.forbidden",
    )


def test_harness_credentials_go_only_to_registered_loopback_origins(world):
    port = world["port"]
    seen = {}

    def record(request):
        seen[str(request.url)] = request.headers.get("authorization")
        return httpx.Response(200)

    path = f"{V1}/executions"
    loopback = [f"http://127.0.0.1:{port}{path}", f"http://localhost:{port}{path}"]
    foreign = [
        f"https://unrelated.example:{port}{path}",
        f"http://unrelated.example:{port}{path}",
        f"https://127.0.0.1:{port}{path}",
        f"http://10.0.0.1:{port}{path}",
    ]
    with httpx.Client(transport=httpx.MockTransport(record)) as client:
        for url in loopback + foreign:
            client.get(url)

    bearer = human_headers(world["url"])["Authorization"]
    assert {url: seen[url] for url in loopback} == {url: bearer for url in loopback}
    assert {url: seen[url] for url in foreign} == {url: None for url in foreign}
