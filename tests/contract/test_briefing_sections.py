# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

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


_BRIEFING_MARKER = "# AgentBeacon Environment"


def _get_system_prompt_from_assign(response):
    assert response["type"] == "command"
    action = response["action"]
    assert action["type"] == "assign"
    return action["agent_config"]["system_prompt"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_lead_briefing_contains_all_sections(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="root-lead-sections")
        _exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert _BRIEFING_MARKER in prompt
        assert "## Delegation" in prompt
        assert "## Escalate" in prompt
        assert "## Coordination" in prompt
        assert "## Messaging" in prompt
        assert "## Recovery" in prompt
        assert "## REST API" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sub_lead_briefing_omits_escalate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-for-sub")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-sub-lead")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        post_worker_sync(ctx["url"], "w-lead")

        mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-sub-lead", "prompt": "do work"},
        )

        response = post_worker_sync(ctx["url"], "w-child")
        prompt = _get_system_prompt_from_assign(response)

        assert "## Delegation" in prompt
        assert "## Escalate" not in prompt
        assert "## Coordination" in prompt
        assert "## Messaging" in prompt
        assert "## Recovery" in prompt
        assert "## REST API" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_briefing_omits_delegation_recovery_escalate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-for-leaf")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-leaf")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("UPDATE executions SET max_depth = 1 WHERE id = ?", (exec_id,))
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        post_worker_sync(ctx["url"], "w-lead")

        mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-leaf", "prompt": "leaf work"},
        )

        response = post_worker_sync(ctx["url"], "w-child")
        prompt = _get_system_prompt_from_assign(response)

        assert "## Delegation" not in prompt
        assert "## Escalate" not in prompt
        assert "## Coordination" in prompt
        assert "## Messaging" in prompt
        assert "## Recovery" not in prompt
        assert "## REST API" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_coordination_section_contains_turn_guidance(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="coordination-check")
        _exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert "end your turn" in prompt
        assert "Authority is separate from communication" in prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_messaging_section_contains_curl_examples(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="messaging-check")
        _exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert 'POST "$AGENTBEACON_API_BASE/api/v1/messages"' in prompt
        assert (
            'PATCH "$AGENTBEACON_API_BASE/api/v1/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>"'
            in prompt
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_section_warns_against_re_delegation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-check")
        _exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        response = post_worker_sync(ctx["url"], "w1")
        prompt = _get_system_prompt_from_assign(response)

        assert "Do NOT immediately re-delegate" in prompt
