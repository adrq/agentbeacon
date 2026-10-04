# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_project,
    seed_test_agent,
)
from tests.mock_agent_helpers import post_worker_sync


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_root_and_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        helper = seed_test_agent(ctx["db_url"], name="helper")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": lead,
                "agent_ids": [lead, helper],
                "parts": [{"text": "test task"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "execution" in data
        assert "session_id" in data
        exec_id = data["execution"]["id"]

        pool_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=5
        )
        assert pool_resp.status_code == 200
        pool = pool_resp.json()
        pool_ids = [e["agent_id"] for e in pool]
        assert lead in pool_ids
        assert helper in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_root_not_in_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        other = seed_test_agent(ctx["db_url"], name="other")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": lead,
                "agent_ids": [other],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_rejects_legacy_agent_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="legacy-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "agent_id": agent,
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 422


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_rejects_empty_agent_ids(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="empty-pool-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent,
                "agent_ids": [],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_agents_returns_config_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="pool-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent, "test")

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=5)
        assert resp.status_code == 200
        pool = resp.json()
        assert len(pool) >= 1
        entry = [e for e in pool if e["agent_id"] == agent][0]
        assert entry["name"] == "pool-agent"
        assert "agent_type" in entry
        assert "description" in entry


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_agents_filters_disabled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        active = seed_test_agent(ctx["db_url"], name="active-agent")
        disabled = seed_test_agent(ctx["db_url"], name="disabled-agent", enabled=False)

        exec_id, _ = create_execution_via_api(ctx["url"], active, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, disabled),
            )
            conn.commit()

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=5)
        assert resp.status_code == 200
        pool = resp.json()
        pool_ids = [e["agent_id"] for e in pool]
        assert active in pool_ids
        assert disabled not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_agent_to_execution_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        new_agent = seed_test_agent(ctx["db_url"], name="new-agent")

        exec_id, _ = create_execution_via_api(ctx["url"], lead, "test")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents",
            json={"agent_id": new_agent},
            timeout=5,
        )
        assert resp.status_code == 204

        pool_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=5
        )
        pool_ids = [e["agent_id"] for e in pool_resp.json()]
        assert new_agent in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_agent_to_pool_with_project_propagation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        new_agent = seed_test_agent(ctx["db_url"], name="propagated-agent")
        project_id = seed_project(ctx["db_url"], name="prop-project", agent_ids=[lead])

        exec_id, _ = create_execution_via_api(
            ctx["url"], lead, "test", project_id=project_id
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents",
            json={"agent_id": new_agent, "add_to_project": True},
            timeout=5,
        )
        assert resp.status_code == 204

        proj_pool_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project_id}/agents", timeout=5
        )
        assert proj_pool_resp.status_code == 200
        proj_pool_ids = [e["agent_id"] for e in proj_pool_resp.json()]
        assert new_agent in proj_pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_agent_from_execution_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        removable = seed_test_agent(ctx["db_url"], name="removable")

        exec_id, _ = create_execution_via_api(
            ctx["url"],
            root_agent_id=lead,
            agent_ids=[lead, removable],
            prompt="test",
        )

        resp = httpx.delete(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents/{removable}", timeout=5
        )
        assert resp.status_code == 204

        pool_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=5
        )
        pool_ids = [e["agent_id"] for e in pool_resp.json()]
        assert removable not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_sessions_endpoint(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="session-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent, "test")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/sessions", timeout=5
        )
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) >= 1

        root = [s for s in sessions if s["session_id"] == session_id]
        assert len(root) == 1
        assert root[0]["role"] == "root-lead"
        assert root[0]["desired"] == "run"
        assert root[0]["executor_state"] == "unassigned"
        assert "agent_name" in root[0]
        assert "hierarchical_name" in root[0]
        assert root[0]["parent_name"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_caller_scoped_to_own_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_a = seed_test_agent(ctx["db_url"], name="agent-a")
        agent_b = seed_test_agent(ctx["db_url"], name="agent-b")

        exec_a, session_a = create_execution_via_api(ctx["url"], agent_a, "task A")
        exec_b, _ = create_execution_via_api(ctx["url"], agent_b, "task B")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_b}/agents",
            headers={"Authorization": f"Bearer {session_a}"},
            timeout=5,
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_pool_crud(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="pool-agent")
        project = create_project_via_api(ctx["url"], "pool-project")
        project_id = project["id"]

        resp = httpx.get(f"{ctx['url']}/api/v1/projects/{project_id}/agents", timeout=5)
        assert resp.status_code == 200
        pool = resp.json()
        pool_ids = [e["agent_id"] for e in pool]
        assert agent in pool_ids

        agent2 = seed_test_agent(ctx["db_url"], name="pool-agent-2")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects/{project_id}/agents",
            json={"agent_id": agent2},
            timeout=5,
        )
        assert resp.status_code == 204

        resp = httpx.get(f"{ctx['url']}/api/v1/projects/{project_id}/agents", timeout=5)
        pool_ids = [e["agent_id"] for e in resp.json()]
        assert agent in pool_ids
        assert agent2 in pool_ids

        resp = httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project_id}/agents/{agent2}", timeout=5
        )
        assert resp.status_code == 204

        resp = httpx.get(f"{ctx['url']}/api/v1/projects/{project_id}/agents", timeout=5)
        pool_ids = [e["agent_id"] for e in resp.json()]
        assert agent in pool_ids
        assert agent2 not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_creation_seeds_all_enabled_agents(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent1 = seed_test_agent(ctx["db_url"], name="enabled-1")
        agent2 = seed_test_agent(ctx["db_url"], name="enabled-2")
        disabled = seed_test_agent(ctx["db_url"], name="disabled-1", enabled=False)

        project = create_project_via_api(ctx["url"], "seeded-project")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/agents", timeout=5
        )
        assert resp.status_code == 200
        pool_ids = [e["agent_id"] for e in resp.json()]
        assert agent1 in pool_ids
        assert agent2 in pool_ids
        assert disabled not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_briefing_reads_from_config_table(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        custom_text = "CUSTOM_DELEGATION_TEXT_FOR_TEST"
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE config SET value = 'true' WHERE name = 'briefing.use_db_overrides'",
            )
            conn.execute(
                "UPDATE config SET value = ? WHERE name = 'briefing.delegation'",
                (custom_text,),
            )
            conn.commit()

        agent = seed_test_agent(ctx["db_url"], name="config-agent")
        create_execution_via_api(ctx["url"], agent, "test")

        response = post_worker_sync(ctx["url"], "w1")
        assert response["type"] == "command"
        system_prompt = response["action"]["agent_config"]["system_prompt"]
        assert custom_text in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_briefing_no_agent_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead-agent")
        helper = seed_test_agent(ctx["db_url"], name="helper-agent")

        create_execution_via_api(
            ctx["url"],
            root_agent_id=lead,
            agent_ids=[lead, helper],
            prompt="test",
        )

        response = post_worker_sync(ctx["url"], "w1")
        assert response["type"] == "command"
        system_prompt = response["action"]["agent_config"]["system_prompt"]
        assert "- `helper-agent`" not in system_prompt
        assert "- `lead-agent`" not in system_prompt
