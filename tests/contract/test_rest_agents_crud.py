"""Contract tests for agent CRUD: POST, GET by id, PATCH, DELETE /api/agents."""

import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_agent_via_api,
    create_execution_via_api,
    scheduler_context,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        data = create_agent_via_api(ctx["url"], "my-agent", description="A test agent")

        assert data["name"] == "my-agent"
        assert data["description"] == "A test agent"
        assert data["agent_type"] == "acp"
        assert data["enabled"] is True
        assert "id" in data
        assert len(data["id"]) == 36
        assert isinstance(data["config"], dict)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_name_collision_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_agent_via_api(ctx["url"], "unique-name")

        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={
                "name": "unique-name",
                "agent_type": "acp",
                "config": {"command": "echo", "args": [], "timeout": 60},
            },
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_invalid_type_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={
                "name": "bad-type-agent",
                "agent_type": "invalid_type",
                "config": {"command": "echo", "args": [], "timeout": 60},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_agent_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "get-test-agent")

        resp = httpx.get(f"{ctx['url']}/api/agents/{created['id']}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == created["id"]
        assert data["name"] == "get-test-agent"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_agent_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/agents/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "original-name")

        resp = httpx.patch(
            f"{ctx['url']}/api/agents/{created['id']}",
            json={"name": "updated-name"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated-name"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_name_collision_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_agent_via_api(ctx["url"], "existing-name")
        second = create_agent_via_api(ctx["url"], "other-name")

        resp = httpx.patch(
            f"{ctx['url']}/api/agents/{second['id']}",
            json={"name": "existing-name"},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_type_rejected(test_database):
    """agent_type is immutable — PATCH with agent_type returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "immutable-type")

        resp = httpx.patch(
            f"{ctx['url']}/api/agents/{created['id']}",
            json={"agent_type": "acp"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_enable_disable(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "toggle-agent")
        assert created["enabled"] is True

        # Disable
        resp = httpx.patch(
            f"{ctx['url']}/api/agents/{created['id']}",
            json={"enabled": False},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Re-enable
        resp = httpx.patch(
            f"{ctx['url']}/api/agents/{created['id']}",
            json={"enabled": True},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "to-delete")

        resp = httpx.delete(f"{ctx['url']}/api/agents/{created['id']}", timeout=5)
        assert resp.status_code == 204

        # Verify excluded from GET list
        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        assert len(resp.json()) == 0

        # Verify excluded from GET by ID
        resp = httpx.get(f"{ctx['url']}/api/agents/{created['id']}", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_agent_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/agents/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_agent_with_active_sessions_returns_409(test_database):
    """Cannot delete an agent that has non-terminal sessions."""
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "busy-agent")

        # Create an execution (leaves session in submitted state)
        create_execution_via_api(ctx["url"], created["id"], "test task")

        resp = httpx.delete(f"{ctx['url']}/api/agents/{created['id']}", timeout=5)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_agent_name_reuse_after_delete(test_database):
    """After soft-deleting an agent, the name can be reused."""
    with scheduler_context(db_url=test_database) as ctx:
        created = create_agent_via_api(ctx["url"], "reuse-name")

        # Delete
        resp = httpx.delete(f"{ctx['url']}/api/agents/{created['id']}", timeout=5)
        assert resp.status_code == 204

        # Create new agent with same name
        new_data = create_agent_via_api(ctx["url"], "reuse-name")
        assert new_data["name"] == "reuse-name"
        assert new_data["id"] != created["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_agent_clears_project_default(test_database):
    """Deleting an agent should clear it as default_agent_id on projects."""
    with scheduler_context(db_url=test_database) as ctx:
        agent = create_agent_via_api(ctx["url"], "default-agent")

        # Create project with this agent as default
        proj_resp = httpx.post(
            f"{ctx['url']}/api/projects",
            json={
                "name": "test-project",
                "path": tempfile.gettempdir(),
                "default_agent_id": agent["id"],
            },
            timeout=5,
        )
        assert proj_resp.status_code == 201
        proj = proj_resp.json()
        assert proj["default_agent_id"] == agent["id"]

        # Delete the agent
        resp = httpx.delete(f"{ctx['url']}/api/agents/{agent['id']}", timeout=5)
        assert resp.status_code == 204

        # Verify project's default_agent_id is now null
        resp = httpx.get(f"{ctx['url']}/api/projects/{proj['id']}", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["default_agent_id"] is None
