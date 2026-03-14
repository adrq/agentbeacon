"""Contract tests for execution pool management, session discovery, and briefing config."""

import json
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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_root_and_pool(test_database):
    """New API: root_agent_id + agent_ids creates pool and root session."""
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        helper = seed_test_agent(ctx["db_url"], name="helper")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "root_agent_id": lead,
                "agent_ids": [lead, helper],
                "parts": [{"kind": "text", "text": "test task"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "execution" in data
        assert "session_id" in data
        exec_id = data["execution"]["id"]

        # Verify pool was populated
        pool_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/agents", timeout=5
        )
        assert pool_resp.status_code == 200
        pool = pool_resp.json()
        pool_ids = [e["agent_id"] for e in pool]
        assert lead in pool_ids
        assert helper in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_root_not_in_pool(test_database):
    """400 when root_agent_id is not in agent_ids."""
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        other = seed_test_agent(ctx["db_url"], name="other")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "root_agent_id": lead,
                "agent_ids": [other],
                "parts": [{"kind": "text", "text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_rejects_legacy_agent_id(test_database):
    """Old API shape (agent_id instead of root_agent_id) is rejected with 422."""
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="legacy-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent,
                "parts": [{"kind": "text", "text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 422


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_rejects_empty_agent_ids(test_database):
    """Empty agent_ids array returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="empty-pool-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "root_agent_id": agent,
                "agent_ids": [],
                "parts": [{"kind": "text", "text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_agents_returns_config_pool(test_database):
    """GET .../agents returns pool entries with agent_id, name, description, agent_type."""
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="pool-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent, "test")

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}/agents", timeout=5)
        assert resp.status_code == 200
        pool = resp.json()
        assert len(pool) >= 1
        entry = [e for e in pool if e["agent_id"] == agent][0]
        assert entry["name"] == "pool-agent"
        assert "agent_type" in entry
        assert "description" in entry


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_agents_filters_disabled(test_database):
    """Disabled agent in junction should not appear in pool response."""
    with scheduler_context(db_url=test_database) as ctx:
        active = seed_test_agent(ctx["db_url"], name="active-agent")
        disabled = seed_test_agent(ctx["db_url"], name="disabled-agent", enabled=False)

        exec_id, _ = create_execution_via_api(ctx["url"], active, "test")

        # Manually insert disabled agent into execution pool
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, disabled),
            )
            conn.commit()

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}/agents", timeout=5)
        assert resp.status_code == 200
        pool = resp.json()
        pool_ids = [e["agent_id"] for e in pool]
        assert active in pool_ids
        assert disabled not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_agent_to_execution_pool(test_database):
    """POST .../agents adds agent; subsequent GET includes it."""
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        new_agent = seed_test_agent(ctx["db_url"], name="new-agent")

        exec_id, _ = create_execution_via_api(ctx["url"], lead, "test")

        # Add new agent to pool
        resp = httpx.post(
            f"{ctx['url']}/api/executions/{exec_id}/agents",
            json={"agent_id": new_agent},
            timeout=5,
        )
        assert resp.status_code == 204

        # Verify it appears in pool
        pool_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/agents", timeout=5
        )
        pool_ids = [e["agent_id"] for e in pool_resp.json()]
        assert new_agent in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_agent_to_pool_with_project_propagation(test_database):
    """add_to_project=true propagates agent to project pool."""
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        new_agent = seed_test_agent(ctx["db_url"], name="propagated-agent")
        project_id = seed_project(ctx["db_url"], name="prop-project", agent_ids=[lead])

        exec_id, _ = create_execution_via_api(
            ctx["url"], lead, "test", project_id=project_id
        )

        # Add with project propagation (default true)
        resp = httpx.post(
            f"{ctx['url']}/api/executions/{exec_id}/agents",
            json={"agent_id": new_agent, "add_to_project": True},
            timeout=5,
        )
        assert resp.status_code == 204

        # Verify it appears in project pool
        proj_pool_resp = httpx.get(
            f"{ctx['url']}/api/projects/{project_id}/agents", timeout=5
        )
        assert proj_pool_resp.status_code == 200
        proj_pool_ids = [e["agent_id"] for e in proj_pool_resp.json()]
        assert new_agent in proj_pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_agent_from_execution_pool(test_database):
    """DELETE .../agents/{id} removes agent from pool."""
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead")
        removable = seed_test_agent(ctx["db_url"], name="removable")

        exec_id, _ = create_execution_via_api(
            ctx["url"],
            root_agent_id=lead,
            agent_ids=[lead, removable],
            prompt="test",
        )

        # Remove from pool
        resp = httpx.delete(
            f"{ctx['url']}/api/executions/{exec_id}/agents/{removable}", timeout=5
        )
        assert resp.status_code == 204

        # Verify removed
        pool_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/agents", timeout=5
        )
        pool_ids = [e["agent_id"] for e in pool_resp.json()]
        assert removable not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_sessions_endpoint(test_database):
    """GET .../sessions returns session discovery with role and status."""
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="session-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent, "test")

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}/sessions", timeout=5)
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) >= 1

        root = [s for s in sessions if s["session_id"] == session_id]
        assert len(root) == 1
        assert root[0]["role"] == "root-lead"
        assert root[0]["status"] == "submitted"
        assert "agent_name" in root[0]
        assert "hierarchical_name" in root[0]
        assert root[0]["parent_name"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_caller_scoped_to_own_execution(test_database):
    """Session bearer for execution A accessing execution B returns 403."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_a = seed_test_agent(ctx["db_url"], name="agent-a")
        agent_b = seed_test_agent(ctx["db_url"], name="agent-b")

        exec_a, session_a = create_execution_via_api(ctx["url"], agent_a, "task A")
        exec_b, _ = create_execution_via_api(ctx["url"], agent_b, "task B")

        # Session A tries to access execution B's agents
        resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_b}/agents",
            headers={"Authorization": f"Bearer {session_a}"},
            timeout=5,
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_pool_crud(test_database):
    """GET/POST/DELETE /api/projects/{id}/agents full lifecycle."""
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="pool-agent")
        project = create_project_via_api(ctx["url"], "pool-project")
        project_id = project["id"]

        # List pool (should include seeded agent since create seeds all enabled)
        resp = httpx.get(f"{ctx['url']}/api/projects/{project_id}/agents", timeout=5)
        assert resp.status_code == 200
        pool = resp.json()
        pool_ids = [e["agent_id"] for e in pool]
        assert agent in pool_ids

        # Add a second agent
        agent2 = seed_test_agent(ctx["db_url"], name="pool-agent-2")
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{project_id}/agents",
            json={"agent_id": agent2},
            timeout=5,
        )
        assert resp.status_code == 204

        # Verify both in pool
        resp = httpx.get(f"{ctx['url']}/api/projects/{project_id}/agents", timeout=5)
        pool_ids = [e["agent_id"] for e in resp.json()]
        assert agent in pool_ids
        assert agent2 in pool_ids

        # Remove one
        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{project_id}/agents/{agent2}", timeout=5
        )
        assert resp.status_code == 204

        # Verify removed
        resp = httpx.get(f"{ctx['url']}/api/projects/{project_id}/agents", timeout=5)
        pool_ids = [e["agent_id"] for e in resp.json()]
        assert agent in pool_ids
        assert agent2 not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_creation_seeds_all_enabled_agents(test_database):
    """New project auto-populates project_agents with all enabled agents."""
    with scheduler_context(db_url=test_database) as ctx:
        agent1 = seed_test_agent(ctx["db_url"], name="enabled-1")
        agent2 = seed_test_agent(ctx["db_url"], name="enabled-2")
        disabled = seed_test_agent(ctx["db_url"], name="disabled-1", enabled=False)

        project = create_project_via_api(ctx["url"], "seeded-project")

        resp = httpx.get(f"{ctx['url']}/api/projects/{project['id']}/agents", timeout=5)
        assert resp.status_code == 200
        pool_ids = [e["agent_id"] for e in resp.json()]
        assert agent1 in pool_ids
        assert agent2 in pool_ids
        assert disabled not in pool_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_briefing_reads_from_config_table(test_database):
    """Modified briefing.delegation in config table appears in new execution's briefing."""
    with scheduler_context(db_url=test_database) as ctx:
        custom_text = "CUSTOM_DELEGATION_TEXT_FOR_TEST"
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE config SET value = ? WHERE name = 'briefing.delegation'",
                (custom_text,),
            )
            conn.commit()

        agent = seed_test_agent(ctx["db_url"], name="config-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent, "test")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        assert custom_text in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_briefing_no_agent_list(test_database):
    """Briefing delegation section does NOT contain agent markdown list items."""
    with scheduler_context(db_url=test_database) as ctx:
        lead = seed_test_agent(ctx["db_url"], name="lead-agent")
        helper = seed_test_agent(ctx["db_url"], name="helper-agent")

        _, session_id = create_execution_via_api(
            ctx["url"],
            root_agent_id=lead,
            agent_ids=[lead, helper],
            prompt="test",
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        # Should NOT have old-style agent list like "- `helper-agent`"
        assert "- `helper-agent`" not in system_prompt
        assert "- `lead-agent`" not in system_prompt
