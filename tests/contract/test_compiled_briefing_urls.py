# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import re

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import post_worker_sync

EXCEPTIONS = ("/api/docs", "/api/versions", "/api/health", "/api/ready", "/api/worker")

UNVERSIONED = re.compile(r"/api/(?!v1/)[A-Za-z_{]")


def _unversioned_product_paths(text: str) -> list[str]:
    found = []
    for match in UNVERSIONED.finditer(text):
        tail = text[match.start() : match.start() + 16]
        if any(tail.startswith(keep) for keep in EXCEPTIONS):
            continue
        found.append(text[match.start() : match.start() + 60])
    return found


def _briefing(db_url, url, agent_name):
    agent_id = seed_test_agent(db_url, name=agent_name)
    create_execution_via_api(url, agent_id, "briefing probe")
    response = post_worker_sync(url, f"worker-{agent_name}")
    assert response["type"] == "command", response
    action = response["action"]
    assert action["type"] == "assign", action
    return action["agent_config"]["system_prompt"]


@DUAL_BACKEND
def test_the_compiled_briefing_has_no_unversioned_product_urls(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        text = _briefing(ctx["db_url"], ctx["url"], "briefing-urls")
        assert _unversioned_product_paths(text) == []
        assert "/api/v1/messages" in text
        assert "/api/v1/escalate" in text


@DUAL_BACKEND
def test_the_briefing_keeps_unversioned_infra_paths(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        text = _briefing(ctx["db_url"], ctx["url"], "briefing-exceptions")
        assert "/api/docs" in text, "/api/docs should remain in the briefing"


@DUAL_BACKEND
def test_the_api_reference_has_no_unversioned_product_urls(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/docs", timeout=10)
        assert resp.status_code == 200
        assert _unversioned_product_paths(resp.text) == []


@DUAL_BACKEND
def test_the_api_reference_documents_the_current_shapes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        text = httpx.get(f"{ctx['url']}/api/docs", timeout=10).text
        assert "question_ids" not in text
        assert '{"batch_id": "uuid", "event_id": "123"}' in text
        assert "next_cursor" in text


@DUAL_BACKEND
def test_the_mcp_tool_descriptions_have_no_unversioned_product_urls(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="briefing-mcp")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {session_id}",
                "Accept": "application/json, text/event-stream",
            },
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        assert _unversioned_product_paths(resp.text) == []
        assert "/api/v1/messages" in resp.text
