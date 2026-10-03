# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import pathlib
import re

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _members(value):
    return set(value)


def _exact(value, expected, label):
    assert _members(value) == expected, (
        f"{label}: unexpected {_members(value) - expected}, "
        f"missing {expected - _members(value)}"
    )


EXECUTION = {
    "id",
    "project_id",
    "parent_execution_id",
    "context_id",
    "desired",
    "outcome",
    "status",
    "title",
    "metadata",
    "max_depth",
    "max_width",
    "sandbox_policy",
    "completion_eligible",
    "created_at",
    "updated_at",
    "completed_at",
}

AGENT = {
    "id",
    "name",
    "description",
    "agent_type",
    "driver_id",
    "config",
    "sandbox_config",
    "system_prompt",
    "enabled",
    "created_at",
    "updated_at",
}

PROJECT = {
    "id",
    "slug",
    "name",
    "path",
    "is_git",
    "settings",
    "created_at",
    "updated_at",
}

DRIVER = {"id", "name", "platform", "config", "created_at", "updated_at"}

PROJECT_AGENT = {"agent_id", "name", "description", "agent_type"}

MODEL_SHAPES = [
    {"id", "label"},
    {"id", "label", "description"},
    {"id", "label", "recommended"},
    {"id", "label", "description", "recommended"},
]

MODEL_SHAPES_SERVED = {
    frozenset({"id", "label"}),
    frozenset({"id", "label", "description"}),
    frozenset({"id", "label", "description", "recommended"}),
}

SESSION = {
    "id",
    "execution_id",
    "parent_session_id",
    "agent_id",
    "agent_session_id",
    "cwd",
    "worktree_path",
    "desired",
    "executor_state",
    "outcome",
    "status",
    "desired_by",
    "worker_id",
    "command_type",
    "parent_notified",
    "recovery_attempts",
    "metadata",
    "sandbox_policy",
    "created_at",
    "updated_at",
    "completed_at",
}

EVENT = {
    "id",
    "execution_id",
    "session_id",
    "event_type",
    "payload",
    "created_at",
}

PAGE = {"items", "next_cursor", "has_more"}

DECISION_BRIEF = {
    "event_id",
    "batch_id",
    "execution_id",
    "execution_title",
    "session_id",
    "status",
    "importance",
    "questions",
    "answer",
    "answered_at",
    "dismissed_at",
    "created_at",
}

DECISION_SUMMARY = {
    "event_id",
    "batch_id",
    "execution_id",
    "execution_title",
    "session_id",
    "status",
    "importance",
    "question_preview",
    "question_count",
    "created_at",
}


def _fixture(ctx, name):
    agent_id = seed_test_agent(ctx["db_url"], name=name)
    exec_id, session_id = create_execution_via_api(
        ctx["url"], agent_id, "wire", title="Wire"
    )
    return agent_id, exec_id, session_id


@DUAL_BACKEND
def test_executions_endpoints(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, _session_id = _fixture(ctx, "wire-executions")

        listed = httpx.get(f"{ctx['url']}/api/v1/executions", timeout=10).json()
        assert isinstance(listed, list)
        _exact(listed[0], EXECUTION, "execution list item")
        assert isinstance(listed[0]["id"], str)

        detail = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=10
        ).json()
        _exact(detail, {"execution", "sessions"}, "execution detail")
        _exact(detail["execution"], EXECUTION, "execution detail execution")
        _exact(detail["sessions"][0], SESSION, "execution detail session")

        sessions = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/sessions", timeout=10
        ).json()
        _exact(
            sessions[0],
            {
                "session_id",
                "agent_name",
                "hierarchical_name",
                "parent_name",
                "role",
                "status",
                "desired",
                "executor_state",
            },
            "session discovery entry",
        )

        pool = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=10
        ).json()
        _exact(pool[0], {"agent_id", "name", "description", "agent_type"}, "pool entry")


@DUAL_BACKEND
def test_sessions_endpoints(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, session_id = _fixture(ctx, "wire-sessions")

        listed = httpx.get(f"{ctx['url']}/api/v1/sessions", timeout=10).json()
        _exact(listed[0], SESSION, "session list item")

        detail = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}", timeout=10
        ).json()
        _exact(detail, SESSION, "session detail")

        posted = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "hello"}]},
            timeout=15,
        ).json()
        _exact(
            posted,
            {"event_id", "session_status", "execution_status"},
            "post message response",
        )
        assert isinstance(posted["event_id"], str)


@DUAL_BACKEND
def test_event_reads(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, session_id = _fixture(ctx, "wire-events")

        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=10
        ).json()
        _exact(page, PAGE, "event page")
        assert page["next_cursor"] is None or isinstance(page["next_cursor"], str)
        assert isinstance(page["has_more"], bool)

        message = next(e for e in page["items"] if e["event_type"] == "message")
        _exact(message, EVENT, "event record")
        assert isinstance(message["id"], str)
        assert message["session_id"] is None or isinstance(message["session_id"], str)
        assert isinstance(message["payload"], dict)
        assert "truncated" not in message
        assert "byte_size" not in message
        assert "omitted_paths" not in message
        assert "envelope_version" not in message
        assert "schema_version" not in message

        session_page = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events", timeout=10
        ).json()
        _exact(session_page, PAGE, "session event page")

        one = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/{message['id']}",
            timeout=10,
        ).json()
        _exact(one, EVENT, "single event")


@DUAL_BACKEND
def test_escalate_and_decisions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, session_id = _fixture(ctx, "wire-decisions")

        created = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Which?",
                        "context": "why",
                        "options": [
                            {"label": "A", "description": "a"},
                            {"label": "B", "description": "b"},
                        ],
                    }
                ]
            },
        )
        _exact(created, {"batch_id", "event_id"}, "escalate response")
        assert isinstance(created["event_id"], str)

        pending = httpx.get(f"{ctx['url']}/api/v1/decisions", timeout=10).json()
        _exact(pending, {"decisions"}, "pending feed")
        brief = pending["decisions"][0]
        _exact(brief, DECISION_BRIEF, "decision brief")
        _exact(
            brief["questions"][0],
            {"question", "context", "options", "index"},
            "decision question",
        )
        _exact(
            brief["questions"][0]["options"][0],
            {"label", "description"},
            "question option",
        )

        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{created['event_id']}", timeout=10
        ).json()
        _exact(detail, DECISION_BRIEF, "decision detail")

        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{created['event_id']}/dismiss", timeout=10
        )
        resolved = httpx.get(
            f"{ctx['url']}/api/v1/decisions",
            params={"state": "resolved"},
            timeout=10,
        ).json()
        _exact(resolved, PAGE, "resolved page")
        _exact(resolved["items"][0], DECISION_SUMMARY, "decision summary")


@DUAL_BACKEND
def test_messages_endpoint(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, session_id = _fixture(ctx, "wire-messages")

        with db_conn(test_database) as conn:
            for payload in (
                '{"role":"ROLE_AGENT","parts":[{"data":{"type":"sender",'
                '"name":"lead/child","session_id":"other-session"}},'
                '{"text":"from a peer"}]}',
                '{"role":"ROLE_USER","parts":[{"text":"from the user"}]}',
            ):
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) "
                    "VALUES (?, ?, 'message', ?)",
                    (_exec_id, session_id, payload),
                )
            conn.commit()

        listed = httpx.get(
            f"{ctx['url']}/api/v1/messages",
            params={"session_id": session_id},
            timeout=10,
        ).json()
        assert listed, "an empty history would assert nothing"
        for message in listed:
            _exact(message, {"id", "sender", "body", "parts", "created_at"}, "message")
            assert isinstance(message["id"], str)
            assert isinstance(message["body"], str)
            assert isinstance(message["parts"], list)
            assert isinstance(message["created_at"], str)

        with_sender = [m for m in listed if m["sender"] is not None]
        without_sender = [m for m in listed if m["sender"] is None]
        assert with_sender and without_sender, (
            "expected messages both with and without a sender"
        )
        _exact(with_sender[0]["sender"], {"name", "session_id"}, "message sender")
        assert isinstance(with_sender[0]["sender"]["name"], str)
        assert isinstance(with_sender[0]["sender"]["session_id"], str)


@DUAL_BACKEND
def test_agents_drivers_projects_and_config(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, _exec_id, _session_id = _fixture(ctx, "wire-catalog")

        agent = httpx.get(f"{ctx['url']}/api/v1/agents/{agent_id}", timeout=10).json()
        _exact(agent, AGENT, "agent")
        assert isinstance(agent["id"], str)
        assert isinstance(agent["name"], str)
        assert isinstance(agent["agent_type"], str)
        assert isinstance(agent["config"], dict)
        assert isinstance(agent["enabled"], bool)
        assert isinstance(agent["created_at"], str)
        assert isinstance(agent["updated_at"], str)
        for nullable in ("description", "driver_id", "sandbox_config", "system_prompt"):
            assert nullable in agent

        drivers = httpx.get(f"{ctx['url']}/api/v1/drivers", timeout=10).json()
        assert isinstance(drivers, list)
        assert drivers, "GET /drivers returned an empty list"
        for driver in drivers:
            _exact(driver, DRIVER, "driver")
            assert isinstance(driver["id"], str)
            assert isinstance(driver["name"], str)
            assert isinstance(driver["platform"], str)
            assert isinstance(driver["config"], dict)
            assert isinstance(driver["created_at"], str)
            assert isinstance(driver["updated_at"], str)

        models = httpx.get(
            f"{ctx['url']}/api/v1/drivers/claude_sdk/models", timeout=10
        ).json()
        assert models, "the catalog is static and non-empty"
        seen_variants = set()
        for model in models:
            members = _members(model)
            assert members in MODEL_SHAPES, f"model: unexpected shape {members}"
            seen_variants.add(frozenset(members))
            assert isinstance(model["id"], str)
            assert isinstance(model["label"], str)
            if "description" in model:
                assert isinstance(model["description"], str)
            if "recommended" in model:
                assert isinstance(model["recommended"], bool)
        assert seen_variants == MODEL_SHAPES_SERVED

        project = create_project_via_api(ctx["url"], "wire-project")
        assert (
            httpx.post(
                f"{ctx['url']}/api/v1/projects/{project['id']}/agents",
                json={"agent_id": agent_id},
                timeout=10,
            ).status_code
            == 204
        )
        pool = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/agents", timeout=10
        ).json()
        assert len(pool) == 1, "an empty pool would assert nothing"
        _exact(pool[0], PROJECT_AGENT, "project agent")
        assert pool[0]["agent_id"] == agent_id
        assert isinstance(pool[0]["name"], str)
        assert isinstance(pool[0]["agent_type"], str)
        assert "description" in pool[0]

        created_server = httpx.post(
            f"{ctx['url']}/api/v1/mcp-servers",
            json={
                "name": "wire-mcp",
                "transport_type": "stdio",
                "config": {"command": "true"},
            },
            timeout=10,
        ).json()
        assert (
            httpx.post(
                f"{ctx['url']}/api/v1/projects/{project['id']}/mcp-servers",
                json={"mcp_server_id": created_server["id"]},
                timeout=10,
            ).status_code
            == 204
        )
        servers = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/mcp-servers", timeout=10
        ).json()
        assert len(servers) == 1, "an empty list would assert nothing"
        for entry in servers:
            _exact(
                entry,
                {"mcp_server_id", "name", "transport_type", "config"},
                "project mcp server",
            )
            assert isinstance(entry["mcp_server_id"], str)
            assert isinstance(entry["name"], str)
            assert isinstance(entry["transport_type"], str)
            assert isinstance(entry["config"], dict)

        assert (
            httpx.post(
                f"{ctx['url']}/api/v1/config",
                json={"name": "wire.probe", "value": "on"},
                timeout=10,
            ).status_code
            == 200
        )
        config = httpx.get(f"{ctx['url']}/api/v1/config", timeout=10).json()
        assert config, "an empty list would assert nothing"
        for entry in config:
            _exact(entry, {"name", "value", "created_at", "updated_at"}, "config entry")
            assert isinstance(entry["name"], str)
            assert isinstance(entry["value"], str)
            assert isinstance(entry["created_at"], str)
            assert isinstance(entry["updated_at"], str)


@DUAL_BACKEND
def test_discovery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        body = httpx.get(f"{ctx['url']}/api/versions", timeout=10).json()
        _exact(
            body,
            {"discovery_version", "server", "rest_versions", "limits"},
            "discovery",
        )
        _exact(body["server"], {"version"}, "server info")
        assert isinstance(body["server"]["version"], str) and body["server"]["version"]
        _exact(body["limits"], {"events", "decisions"}, "limits")


@DUAL_BACKEND
def test_conditional_members_are_covered_per_branch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, session_id = _fixture(ctx, "wire-branches")

        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload, msg_seq) "
                "VALUES (?, ?, 'message', ?, 7)",
                (exec_id, session_id, '{"role":"ROLE_AGENT","parts":[]}'),
            )
            conn.commit()
        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=10
        ).json()
        with_seq = next(e for e in page["items"] if e.get("msg_seq") == 7)
        _exact(with_seq, EVENT | {"msg_seq"}, "event with msg_seq")

        oversized = '{"role":"ROLE_AGENT","parts":[{"text":"' + "x" * 200_000 + '"}]}'
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, oversized),
            )
            conn.commit()
        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=15
        ).json()
        truncated = next(e for e in page["items"] if e.get("truncated"))
        _exact(truncated, EVENT | {"truncated", "byte_size"}, "truncated event")
        assert truncated["truncated"] is True
        assert isinstance(truncated["byte_size"], int)

        attachment = (
            '{"role":"ROLE_USER","parts":['
            '{"text":"see attached"},'
            '{"raw":"' + "QUJDRA==" * 40_000 + '","mediaType":"image/png",'
            '"filename":"big.png"}]}'
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, attachment),
            )
            conn.commit()
        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=15
        ).json()
        served = next(e for e in page["items"] if e.get("omitted_paths"))
        _exact(
            served,
            EVENT | {"truncated", "byte_size", "omitted_paths"},
            "event with an omitted attachment",
        )
        assert served["omitted_paths"] == ["parts[1].raw"]
        assert served["payload"]["parts"][1]["raw"] == ""
        assert served["payload"]["parts"][1]["filename"] == "big.png"
        assert served["payload"]["parts"][0]["text"] == "see attached"

        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Answered?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "yes"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": decision["event_id"],
                        }
                    },
                ]
            },
            timeout=15,
        )
        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{decision['event_id']}", timeout=10
        ).json()
        _exact(detail, DECISION_BRIEF, "answered decision brief")
        assert detail["answered_at"] is not None
        assert detail["dismissed_at"] is None
        assert "truncated" not in detail, "absent unless the answer was shortened"

        long_decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Long?"}]}
        )
        marker = json.dumps(
            {
                "parts": [
                    {
                        "data": {
                            "type": "question_answer",
                            "batch_id": long_decision["batch_id"],
                            "escalation_event_id": int(long_decision["event_id"]),
                            "resolved_event_id": int(long_decision["event_id"]) + 1,
                            "resolved_at": "2026-01-01T00:00:00Z",
                            "answer_text": "shortened",
                            "truncated": True,
                        }
                    }
                ]
            }
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, marker),
            )
            conn.commit()

        shortened = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{long_decision['event_id']}", timeout=10
        ).json()
        _exact(shortened, DECISION_BRIEF | {"truncated"}, "shortened decision brief")
        assert shortened["truncated"] is True
        assert shortened["answer"] == "shortened"


@DUAL_BACKEND
def test_served_events_carry_no_schema_version(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="wire-schema")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, '{"role":"ROLE_AGENT","parts":[{"text":"v2"}]}'),
            )
            conn.commit()

        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=10
        ).json()
        for item in page["items"]:
            assert "schema_version" not in item


_API_DIR = pathlib.Path(__file__).resolve().parents[2] / "scheduler" / "src" / "api"


def _registered_routes():
    v1 = (_API_DIR / "v1" / "mod.rs").read_text()
    modules = sorted(set(re.findall(r"\.merge\((\w+)::routes\(\)\)", v1)))
    assert modules, "no modules found: the router's shape changed"
    routes = set()
    for module in modules:
        source = (_API_DIR / f"{module}.rs").read_text()
        for chunk in source.split(".route(")[1:]:
            named = re.match(r'\s*"([^"]+)"\s*,', chunk)
            if not named:
                continue
            body = chunk[named.end() :].split(".route(")[0]
            for verb in re.findall(r"\b(get|post|put|patch|delete)\(", body[:600]):
                routes.add((verb.upper(), named.group(1)))
    return routes


EXACT_CHECKED = {
    ("GET", "/agents"),
    ("POST", "/agents"),
    ("GET", "/agents/{id}"),
    ("PATCH", "/agents/{id}"),
    ("GET", "/config"),
    ("POST", "/config"),
    ("GET", "/decisions"),
    ("GET", "/decisions/{event_id}"),
    ("GET", "/drivers"),
    ("POST", "/drivers"),
    ("GET", "/drivers/{id}"),
    ("PATCH", "/drivers/{id}"),
    ("GET", "/drivers/{platform}/descriptor"),
    ("GET", "/drivers/{platform}/models"),
    ("POST", "/escalate"),
    ("POST", "/escalate/{event_id}/dismiss"),
    ("GET", "/executions"),
    ("POST", "/executions"),
    ("GET", "/executions/{id}"),
    ("POST", "/executions/{id}/terminate"),
    ("GET", "/executions/{id}/events"),
    ("GET", "/executions/{execution_id}/events/{event_id}"),
    ("GET", "/executions/{id}/agents"),
    ("GET", "/executions/{id}/sessions"),
    ("GET", "/mcp-servers"),
    ("POST", "/mcp-servers"),
    ("GET", "/mcp-servers/{id}"),
    ("PATCH", "/mcp-servers/{id}"),
    ("GET", "/messages"),
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("GET", "/projects/{id}"),
    ("PATCH", "/projects/{id}"),
    ("GET", "/projects/{id}/agents"),
    ("GET", "/projects/{id}/mcp-servers"),
    ("GET", "/sessions"),
    ("GET", "/sessions/{id}"),
    ("GET", "/sessions/{id}/events"),
    ("POST", "/sessions/{id}/message"),
    ("GET", "/wiki/search"),
    ("GET", "/wiki/tags"),
    ("POST", "/wiki/tags/{tag_id}/members"),
    ("PATCH", "/wiki/tags/{tag_id}/members/{project}"),
    ("GET", "/projects/{project_id}/wiki/pages"),
    ("GET", "/projects/{project_id}/wiki/pages/{slug}"),
    ("PUT", "/projects/{project_id}/wiki/pages/{slug}"),
    ("PATCH", "/projects/{project_id}/wiki/pages/{slug}"),
    ("GET", "/projects/{project_id}/wiki/pages/{slug}/revisions"),
    ("GET", "/projects/{project_id}/wiki/pages/{slug}/revisions/{rev}"),
    ("GET", "/projects/{project_id}/wiki/tags"),
    ("GET", "/projects/{project_id}/wiki/changes"),
}

EXCLUDED = {
    ("DELETE", "/agents/{id}"): "204 with no body",
    ("DELETE", "/drivers/{id}"): "204 with no body",
    ("DELETE", "/projects/{id}"): "204 with no body",
    ("POST", "/projects/{id}/agents"): "204 with no body",
    ("DELETE", "/projects/{id}/agents/{agent_id}"): "204 with no body",
    ("POST", "/projects/{id}/mcp-servers"): "204 with no body",
    ("DELETE", "/projects/{id}/mcp-servers/{mcp_server_id}"): "204 with no body",
    ("POST", "/executions/{id}/agents"): "204 with no body",
    ("DELETE", "/executions/{id}/agents/{agent_id}"): "204 with no body",
    ("DELETE", "/mcp-servers/{id}"): "204 with no body",
    ("DELETE", "/sessions/{id}/worktree"): "204 with no body",
    ("DELETE", "/projects/{project_id}/wiki/pages/{slug}"): "204 with no body",
    (
        "DELETE",
        "/projects/{project_id}/wiki/subscriptions/{sub_id}",
    ): "204 with no body",
    ("DELETE", "/wiki/tags/{tag_id}/members/{project}"): "204 with no body",
    ("GET", "/executions/{id}/events/stream"): "an SSE stream, not a document",
    ("GET", "/projects/{project_id}/wiki/export"): "streams a file, not a document",
    ("GET", "/sessions/{id}/worktree"): (
        "needs a real git worktree; a seeded session answers 404/400 instead of a shape"
    ),
    ("GET", "/sessions/{id}/worktree/diff"): "needs a real git worktree",
    ("GET", "/sessions/{id}/worktree/branches"): "needs a real git worktree",
    ("POST", "/sessions/{id}/stop"): (
        "success needs a session in a specific lifecycle state"
    ),
    ("POST", "/sessions/{id}/terminate"): (
        "success needs a session in a specific lifecycle state"
    ),
    ("POST", "/sessions/{id}/continue"): (
        "success needs a session in a specific lifecycle state"
    ),
    ("POST", "/sessions/{id}/recover"): (
        "success needs a session in a specific lifecycle state"
    ),
    ("GET", "/projects/{project_id}/wiki/subscriptions"): (
        "requires a `subscriber` query parameter"
    ),
    ("POST", "/projects/{project_id}/wiki/subscriptions"): (
        "creates a subscription; not a static shape"
    ),
    ("POST", "/messages"): "requires an authenticated session and a live recipient",
}

_ROUTES = _registered_routes()
_dispositioned = EXACT_CHECKED | set(EXCLUDED)
_undispositioned = _ROUTES - _dispositioned
_stale = _dispositioned - _ROUTES
assert not _undispositioned, (
    "these v1 routes are neither exact-checked nor excluded here: "
    f"{sorted(_undispositioned)}"
)
assert not _stale, f"these entries name routes that no longer exist: {sorted(_stale)}"

WIKI_PAGE_SUMMARY = {
    "page_id",
    "project_id",
    "project_slug",
    "slug",
    "title",
    "tags",
    "access",
    "revision_number",
    "updated_at",
}

WIKI_PAGE = WIKI_PAGE_SUMMARY - {"page_id"} | {
    "id",
    "body",
    "created_at",
}

WIKI_PAGE_WRITTEN = WIKI_PAGE - {"access"}
WIKI_REVISION_DETAIL = {"revision_number", "title", "body", "created_at"}
WIKI_TAG_MEMBER = {
    "id",
    "tag_id",
    "project_id",
    "project_slug",
    "access_level",
    "created_at",
}
PROJECT_MCP_SERVER = {"mcp_server_id", "name", "transport_type", "config"}
CONFIG_ENTRY = {"name", "value", "created_at", "updated_at"}

WIKI_PROJECT_TAG = {"tag_id", "name", "shared", "page_count"}
WIKI_GLOBAL_TAG = {"tag_id", "tag", "members"}
MCP_SERVER = {"id", "name", "transport_type", "config", "created_at", "updated_at"}
WIKI_REVISION = {"revision_number", "title", "created_at"}
WIKI_CHANGE = WIKI_REVISION | {"project_id", "project_slug", "slug"}


@DUAL_BACKEND
def test_the_rest_of_the_v1_surface(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="wire-surface")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        project = create_project_via_api(ctx["url"], "wire-surface-project")

        agents = httpx.get(f"{ctx['url']}/api/v1/agents", timeout=10).json()
        assert agents, "the seeded agent is not listed"
        for agent in agents:
            _exact(agent, AGENT, "agent list item")

        got = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}", timeout=10
        ).json()
        _exact(got, PROJECT, "project")
        assert isinstance(got["is_git"], bool)
        assert isinstance(got["settings"], dict)

        descriptor = httpx.get(
            f"{ctx['url']}/api/v1/drivers/claude_sdk/descriptor", timeout=10
        ).json()
        _exact(
            descriptor, {"platform", "label", "fields", "schema"}, "driver descriptor"
        )
        assert isinstance(descriptor["fields"], list)
        assert isinstance(descriptor["schema"], dict)

        made = httpx.post(
            f"{ctx['url']}/api/v1/mcp-servers",
            json={
                "name": "wire-surface-mcp",
                "transport_type": "stdio",
                "config": {"command": "true"},
            },
            timeout=10,
        )
        assert made.status_code == 201, made.text
        _exact(made.json(), MCP_SERVER, "created mcp server")

        servers = httpx.get(f"{ctx['url']}/api/v1/mcp-servers", timeout=10).json()
        assert servers, "the server just created is not listed"
        for server in servers:
            _exact(server, MCP_SERVER, "mcp server")
            assert isinstance(server["id"], str)
            assert isinstance(server["name"], str)
            assert isinstance(server["transport_type"], str)
            assert isinstance(server["config"], dict)

        created = httpx.put(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/surface",
            json={
                "title": "Surface",
                "body": "body text",
                "tags": ["wire-surface-tag"],
            },
            timeout=10,
        )
        assert created.status_code == 201, created.text
        _exact(created.json(), WIKI_PAGE_WRITTEN, "created wiki page")
        edited = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/surface",
            json={
                "revision_number": created.json()["revision_number"],
                "edits": [{"old_string": "body text", "new_string": "edited body"}],
            },
            timeout=10,
        )
        assert edited.status_code == 200, edited.text
        _exact(edited.json(), WIKI_PAGE_WRITTEN, "edited wiki page")

        page = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/surface",
            timeout=10,
        ).json()
        _exact(page, WIKI_PAGE, "wiki page")
        assert isinstance(page["tags"], list)
        assert isinstance(page["revision_number"], int)

        listed = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages", timeout=10
        ).json()
        assert listed, "the page just written is not listed"
        for item in listed:
            _exact(item, WIKI_PAGE_SUMMARY, "wiki page summary")

        found = httpx.get(
            f"{ctx['url']}/api/v1/wiki/search", params={"q": "Surface"}, timeout=10
        ).json()
        assert found, "the page just written is not findable"
        for hit in found:
            _exact(
                hit,
                (WIKI_PAGE_SUMMARY - {"access"}) | {"score", "updated_by"},
                "wiki search hit",
            )

        revisions = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/surface/revisions",
            timeout=10,
        ).json()
        assert revisions, "the edit above did not archive a revision"
        for revision in revisions:
            _exact(revision, WIKI_REVISION, "wiki revision")
            assert isinstance(revision["revision_number"], int)
            assert isinstance(revision["title"], str)

        changes = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/changes", timeout=10
        ).json()
        assert changes, "the edit above is missing from the changes feed"
        for change in changes:
            _exact(change, WIKI_CHANGE, "wiki change")
            assert isinstance(change["slug"], str)

        project_tags = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/tags", timeout=10
        ).json()
        assert project_tags, "the tag written with the page is not listed"
        for tag in project_tags:
            _exact(tag, WIKI_PROJECT_TAG, "wiki project tag")
            assert isinstance(tag["shared"], bool)
            assert isinstance(tag["page_count"], int)

        global_tags = httpx.get(f"{ctx['url']}/api/v1/wiki/tags", timeout=10).json()
        assert isinstance(global_tags, list)
        for tag in global_tags:
            _exact(tag, WIKI_GLOBAL_TAG, "wiki global tag")
            assert isinstance(tag["members"], list)

        terminated = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=15
        ).json()
        _exact(terminated, {"execution"}, "terminate response")
        _exact(terminated["execution"], EXECUTION, "terminated execution")


@DUAL_BACKEND
def test_every_mutation_serves_its_own_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = f"{ctx['url']}/api/v1"

        drivers = httpx.get(f"{url}/drivers", timeout=10).json()
        claude = next(d for d in drivers if d["platform"] == "claude_sdk")
        created_agent = httpx.post(
            f"{url}/agents",
            json={
                "name": "wire-mutation-agent",
                "driver_id": claude["id"],
                "config": {"model": "claude-sonnet-5"},
            },
            timeout=10,
        )
        assert created_agent.status_code == 201, created_agent.text
        _exact(created_agent.json(), AGENT, "created agent")
        agent_id = created_agent.json()["id"]

        updated_agent = httpx.patch(
            f"{url}/agents/{agent_id}",
            json={"name": "wire-mutation-agent-2"},
            timeout=10,
        )
        assert updated_agent.status_code == 200, updated_agent.text
        _exact(updated_agent.json(), AGENT, "updated agent")
        assert updated_agent.json()["name"] == "wire-mutation-agent-2"

        copilot = next(d for d in drivers if d["platform"] == "copilot_sdk")
        assert (
            httpx.delete(f"{url}/drivers/{copilot['id']}", timeout=10).status_code
            == 204
        )
        created_driver = httpx.post(
            f"{url}/drivers",
            json={
                "name": "wire-mutation-driver",
                "platform": "copilot_sdk",
                "config": {},
            },
            timeout=10,
        )
        assert created_driver.status_code == 201, created_driver.text
        _exact(created_driver.json(), DRIVER, "created driver")
        driver_id = created_driver.json()["id"]

        got_driver = httpx.get(f"{url}/drivers/{driver_id}", timeout=10)
        assert got_driver.status_code == 200, got_driver.text
        _exact(got_driver.json(), DRIVER, "driver detail")
        updated_driver = httpx.patch(
            f"{url}/drivers/{driver_id}",
            json={"name": "wire-mutation-driver-2"},
            timeout=10,
        )
        assert updated_driver.status_code == 200, updated_driver.text
        _exact(updated_driver.json(), DRIVER, "updated driver")

        project = create_project_via_api(ctx["url"], "wire-mutation-project")
        _exact(project, PROJECT, "created project")
        listed_projects = httpx.get(f"{url}/projects", timeout=10).json()
        assert listed_projects, "the project just created is not listed"
        for entry in listed_projects:
            _exact(entry, PROJECT, "project list item")
        updated_project = httpx.patch(
            f"{url}/projects/{project['id']}",
            json={"name": "wire-mutation-project-2"},
            timeout=10,
        )
        assert updated_project.status_code == 200, updated_project.text
        _exact(updated_project.json(), PROJECT, "updated project")
        assert updated_project.json()["name"] == "wire-mutation-project-2"

        server_id = httpx.post(
            f"{url}/mcp-servers",
            json={
                "name": "wire-mutation-mcp",
                "transport_type": "stdio",
                "config": {"command": "true"},
            },
            timeout=10,
        ).json()["id"]
        got_server = httpx.get(f"{url}/mcp-servers/{server_id}", timeout=10)
        assert got_server.status_code == 200, got_server.text
        _exact(got_server.json(), MCP_SERVER, "mcp server detail")
        updated_server = httpx.patch(
            f"{url}/mcp-servers/{server_id}",
            json={"name": "wire-mutation-mcp-2"},
            timeout=10,
        )
        assert updated_server.status_code == 200, updated_server.text
        _exact(updated_server.json(), MCP_SERVER, "updated mcp server")

        assert (
            httpx.post(
                f"{url}/projects/{project['id']}/mcp-servers",
                json={"mcp_server_id": server_id},
                timeout=10,
            ).status_code
            == 204
        )
        project_servers = httpx.get(
            f"{url}/projects/{project['id']}/mcp-servers", timeout=10
        ).json()
        assert project_servers, "the server just attached is not listed"
        for entry in project_servers:
            _exact(entry, PROJECT_MCP_SERVER, "project mcp server")

        stored = httpx.post(
            f"{url}/config",
            json={"name": "wire_mutation_key", "value": "v"},
            timeout=10,
        )
        assert stored.status_code == 200, stored.text
        _exact(stored.json(), CONFIG_ENTRY, "config write")
        assert stored.json()["value"] == "v"

        seeded = seed_test_agent(ctx["db_url"], name="wire-mutation-seed")
        made = httpx.post(
            f"{url}/executions",
            json={
                "root_agent_id": seeded,
                "agent_ids": [seeded],
                "parts": [{"text": "hello"}],
                "title": "wire mutation",
                "cwd": "/tmp",
            },
            timeout=20,
        )
        assert made.status_code == 201, made.text
        _exact(made.json(), {"execution", "session_id"}, "created execution envelope")
        assert isinstance(made.json()["session_id"], str)
        _exact(made.json()["execution"], EXECUTION, "created execution")

        _exec_id, session_id = create_execution_via_api(ctx["url"], seeded, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Dismissed?"}]}
        )
        dismissed = httpx.post(
            f"{url}/escalate/{decision['event_id']}/dismiss", timeout=10
        )
        assert dismissed.status_code == 200, dismissed.text
        _exact(dismissed.json(), {"status"}, "dismiss response")
        assert dismissed.json()["status"] == "dismissed"

        httpx.put(
            f"{url}/projects/{project['id']}/wiki/pages/mutation",
            json={"title": "Mutation", "body": "first", "tags": ["wire-mutation-tag"]},
            timeout=10,
        )
        first_revision = httpx.patch(
            f"{url}/projects/{project['id']}/wiki/pages/mutation",
            json={
                "revision_number": 1,
                "edits": [{"old_string": "first", "new_string": "second"}],
            },
            timeout=10,
        )
        assert first_revision.status_code == 200, first_revision.text
        archived = httpx.get(
            f"{url}/projects/{project['id']}/wiki/pages/mutation/revisions", timeout=10
        ).json()
        assert archived, "the edit above did not archive a revision"
        one = httpx.get(
            f"{url}/projects/{project['id']}/wiki/pages/mutation/revisions/"
            f"{archived[0]['revision_number']}",
            timeout=10,
        )
        assert one.status_code == 200, one.text
        _exact(one.json(), WIKI_REVISION_DETAIL, "wiki revision detail")

        tag = next(
            t
            for t in httpx.get(f"{url}/wiki/tags", timeout=10).json()
            if t["tag"] == "wire-mutation-tag"
        )
        other = create_project_via_api(ctx["url"], "wire-mutation-member")
        admitted = httpx.post(
            f"{url}/wiki/tags/{tag['tag_id']}/members",
            json={
                "project": other["slug"],
                "access_level": "read",
                "acknowledge_share": True,
            },
            timeout=10,
        )
        assert admitted.status_code == 201, admitted.text
        _exact(admitted.json(), WIKI_TAG_MEMBER, "admitted tag member")
        raised = httpx.patch(
            f"{url}/wiki/tags/{tag['tag_id']}/members/{other['slug']}",
            json={"access_level": "read_write"},
            timeout=10,
        )
        assert raised.status_code == 200, raised.text
        _exact(raised.json(), WIKI_TAG_MEMBER, "updated tag member")
        assert raised.json()["access_level"] == "read_write"
