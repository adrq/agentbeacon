"""Contract tests for agent CRUD: POST, GET by id, PATCH, DELETE /api/agents."""

import httpx
import pytest

from tests.testhelpers import (
    create_agent_via_api,
    create_execution_via_api,
    db_conn,
    ensure_driver_via_api,
    scheduler_context,
    seed_test_agent,
    seed_test_driver,
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

        driver_id = ensure_driver_via_api(ctx["url"])
        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={
                "name": "unique-name",
                "driver_id": driver_id,
                "config": {"command": "echo", "args": [], "timeout": 60},
            },
            timeout=5,
        )
        assert resp.status_code == 409


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


# --- Driver relationship tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_with_driver_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(
            ctx["db_url"], name="claude-driver", platform="claude_sdk"
        )
        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={
                "name": "my-agent",
                "driver_id": driver_id,
                "config": {"command": "test"},
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["driver_id"] == driver_id
        assert data["agent_type"] == "claude_sdk"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_without_driver_id_returns_422(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={"name": "no-driver", "config": {"command": "test"}},
            timeout=5,
        )
        assert resp.status_code == 422


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_with_invalid_driver_id_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={
                "name": "bad-driver-agent",
                "driver_id": "nonexistent-id",
                "config": {"command": "test"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_response_includes_driver_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="driver-resp-test")
        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        assert resp.status_code == 200
        agent = resp.json()[0]
        assert "driver_id" in agent
        assert agent["driver_id"] is not None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_agent_driver_id_rejected_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="patch-driver-test")
        resp = httpx.patch(
            f"{ctx['url']}/api/agents/{agent_id}",
            json={"driver_id": "some-other-id"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_type_derived_from_driver(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(
            ctx["db_url"], name="copilot-drv", platform="copilot_sdk"
        )
        resp = httpx.post(
            f"{ctx['url']}/api/agents",
            json={
                "name": "derived-type-agent",
                "driver_id": driver_id,
                "config": {"command": "copilot"},
            },
            timeout=5,
        )
        assert resp.status_code == 201
        assert resp.json()["agent_type"] == "copilot_sdk"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_driver_relational_integrity(test_database):
    """Agent's driver_id FK resolves to a driver with matching platform."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="migration-test", agent_type="copilot_sdk"
        )
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT a.driver_id, d.platform FROM agents a JOIN drivers d ON a.driver_id = d.id WHERE a.id = ?",
                (agent_id,),
            ).fetchone()
        assert row is not None
        assert row[1] == "copilot_sdk"
