# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import tempfile
import uuid

import pytest
import requests

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import post_worker_sync


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_includes_project_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_id = None
        with db_conn(ctx["db_url"]) as conn:
            project_id = str(uuid.uuid4())
            project_path = tempfile.gettempdir()
            conn.execute(
                "INSERT INTO projects (id, name, slug, path) VALUES (?, ?, ?, ?)",
                (project_id, "test-project", project_id, project_path),
            )
            conn.commit()

        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task", project_id=project_id
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert payload["project_id"] == project_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_has_system_prompt_with_briefing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")

        create_execution_via_api(ctx["url"], agent_id, "test task")

        response = post_worker_sync(ctx["url"], "w1")
        assert response["type"] == "command"
        action = response["action"]
        assert action["type"] == "assign"
        system_prompt = action["agent_config"]["system_prompt"]
        assert "AgentBeacon Environment" in system_prompt
        assert "root lead" in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_rejects_agent_not_in_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        seed_test_agent(ctx["db_url"], name="outsider-agent")

        _, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "test task"
        )

        data = mcp_call(
            ctx["url"],
            lead_session_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "outsider-agent", "prompt": "do work"},
            },
        )

        assert data["error"]["code"] == -32602
        assert "not available in this execution" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_gets_briefing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        root_resp = post_worker_sync(ctx["url"], "w-root")
        assert root_resp["type"] == "command"
        response = post_worker_sync(ctx["url"], "w-child")
        assert response["type"] == "command"
        action = response["action"]
        assert action["type"] == "assign"
        assert action["session_id"] == child_session_id
        system_prompt = action["agent_config"]["system_prompt"]
        assert "AgentBeacon Environment" in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_is_sub_lead(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        post_worker_sync(ctx["url"], "w-root")
        response = post_worker_sync(ctx["url"], "w-child")
        assert response["type"] == "command"
        action = response["action"]
        assert action["session_id"] == child_session_id
        system_prompt = action["agent_config"]["system_prompt"]
        assert "sub-lead" in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_briefing_omits_delegation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET max_depth = 1 WHERE id = ?",
                (exec_id,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        post_worker_sync(ctx["url"], "w-root")
        response = post_worker_sync(ctx["url"], "w-child")
        assert response["type"] == "command"
        action = response["action"]
        assert action["session_id"] == child_session_id
        system_prompt = action["agent_config"]["system_prompt"]
        assert "leaf" in system_prompt
        assert "## Delegation" not in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_api_docs_endpoint(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = requests.get(f"{ctx['url']}/api/docs")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        assert "AgentBeacon REST API Reference" in resp.text
        assert "POST /api/v1/messages" in resp.text
        assert "revision_number" in resp.text
        assert "Agent Pool" in resp.text
        assert "Running Sessions" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_system_prompt_from_column(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"],
            name="prompt-agent",
            system_prompt="You are a Rust expert.",
        )

        create_execution_via_api(ctx["url"], agent_id, "test task")

        response = post_worker_sync(ctx["url"], "w1")
        assert response["type"] == "command"
        action = response["action"]
        assert action["type"] == "assign"
        system_prompt = action["agent_config"]["system_prompt"]
        assert "AgentBeacon Environment" in system_prompt
        assert "You are a Rust expert." in system_prompt
