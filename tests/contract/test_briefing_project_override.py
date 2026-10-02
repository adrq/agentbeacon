# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import uuid

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    post_worker_sync,
)


def _enable_db_overrides(db_url):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE config SET value = 'true' WHERE name = 'briefing.use_db_overrides'",
        )
        conn.commit()


def _create_project(db_url, settings=None):
    project_id = str(uuid.uuid4())
    settings_json = json.dumps(settings) if settings else "{}"
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, slug, path, settings) VALUES (?, ?, ?, ?, ?)",
            (
                project_id,
                f"test-project-{project_id[:8]}",
                project_id,
                f"/tmp/proj-{project_id[:8]}",
                settings_json,
            ),
        )
        conn.commit()
    return project_id


def _get_system_prompt_from_assign(response):
    assert response["type"] == "command"
    action = response["action"]
    assert action["type"] == "assign"
    return action["agent_config"]["system_prompt"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_override_takes_precedence(test_database):
    custom_delegation = "CUSTOM: Use our special delegation protocol."
    with scheduler_context(db_url=test_database) as ctx:
        _enable_db_overrides(ctx["db_url"])
        project_id = _create_project(
            ctx["db_url"],
            settings={"briefing": {"delegation": custom_delegation}},
        )
        agent_id = seed_test_agent(ctx["db_url"], name="override-test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_id, agent_id),
            )
            conn.commit()

        _exec_id, _session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello", project_id=project_id
        )
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert custom_delegation in prompt
        assert "delegate` MCP tool" not in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_missing_override_falls_through_to_default(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_id = _create_project(ctx["db_url"], settings={})
        agent_id = seed_test_agent(ctx["db_url"], name="fallthrough-test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_id, agent_id),
            )
            conn.commit()

        _exec_id, _session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello", project_id=project_id
        )
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert "/api/v1/executions/$AGENTBEACON_EXECUTION_ID/agents" in prompt
        assert "## Delegation" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_partial_override_only_affects_specified_section(test_database):
    custom_coordination = "CUSTOM: Our team coordination rules."
    with scheduler_context(db_url=test_database) as ctx:
        _enable_db_overrides(ctx["db_url"])
        project_id = _create_project(
            ctx["db_url"],
            settings={"briefing": {"coordination": custom_coordination}},
        )
        agent_id = seed_test_agent(ctx["db_url"], name="partial-override")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_id, agent_id),
            )
            conn.commit()

        _exec_id, _session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello", project_id=project_id
        )
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert custom_coordination in prompt
        assert "delegate` MCP tool" in prompt
        assert "Do NOT immediately re-delegate" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_override_isolation(test_database):
    custom_text = "CUSTOM: Project A specific delegation."
    with scheduler_context(db_url=test_database) as ctx:
        _enable_db_overrides(ctx["db_url"])
        project_a = _create_project(
            ctx["db_url"],
            settings={"briefing": {"delegation": custom_text}},
        )
        project_b = _create_project(ctx["db_url"], settings={})

        agent_id = seed_test_agent(ctx["db_url"], name="isolation-agent")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_a, agent_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_b, agent_id),
            )
            conn.commit()

        _exec_a, _sess_a = create_execution_via_api(
            ctx["url"], agent_id, "hello a", project_id=project_a
        )
        resp_a = post_worker_sync(ctx["url"], "w-a")
        prompt_a = _get_system_prompt_from_assign(resp_a)
        assert custom_text in prompt_a

        _exec_b, _sess_b = create_execution_via_api(
            ctx["url"], agent_id, "hello b", project_id=project_b
        )
        resp_b = post_worker_sync(ctx["url"], "w-b")
        prompt_b = _get_system_prompt_from_assign(resp_b)
        assert custom_text not in prompt_b
        assert "delegate` MCP tool" in prompt_b


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_override_propagates_to_child_session(test_database):
    custom_coordination = "CUSTOM: Child agents must follow our coordination protocol."
    with scheduler_context(db_url=test_database) as ctx:
        _enable_db_overrides(ctx["db_url"])
        project_id = _create_project(
            ctx["db_url"],
            settings={"briefing": {"coordination": custom_coordination}},
        )
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-propagation")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-propagation")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_id, lead_agent_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO project_agents (project_id, agent_id) VALUES (?, ?)",
                (project_id, child_agent_id),
            )
            conn.commit()

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate", project_id=project_id
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        lead_response = post_worker_sync(ctx["url"], "w-lead")
        lead_prompt = _get_system_prompt_from_assign(lead_response)
        assert custom_coordination in lead_prompt

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-propagation", "prompt": "do child work"},
        )
        child_payload = json.loads(result["content"][0]["text"])
        child_session_id = child_payload["session_id"]

        child_response = post_worker_sync(ctx["url"], "w-child")
        child_prompt = _get_system_prompt_from_assign(child_response)

        assert custom_coordination in child_prompt
        assert child_response["action"]["session_id"] == child_session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_use_db_overrides_false_ignores_config_table(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        custom_text = "CUSTOM_SHOULD_NOT_APPEAR"
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE config SET value = ? WHERE name = 'briefing.delegation'",
                (custom_text,),
            )
            conn.commit()

        agent_id = seed_test_agent(ctx["db_url"], name="no-override-test")
        _exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert custom_text not in prompt
        assert "long-lived" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_use_db_overrides_true_reads_config_table(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _enable_db_overrides(ctx["db_url"])
        custom_text = "CUSTOM_SHOULD_APPEAR_IN_BRIEFING"
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE config SET value = ? WHERE name = 'briefing.delegation'",
                (custom_text,),
            )
            conn.commit()

        agent_id = seed_test_agent(ctx["db_url"], name="override-enabled-test")
        _exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert custom_text in prompt
