"""Contract tests for /api/drivers endpoint."""

import httpx
import pytest

from tests.testhelpers import (
    scheduler_context,
    seed_test_driver,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_drivers_returns_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/drivers", timeout=5)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_driver(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/drivers",
            json={"name": "my-claude", "platform": "claude_sdk"},
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-claude"
        assert data["platform"] == "claude_sdk"
        assert data["config"] == {}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_driver_name_collision_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_driver(ctx["db_url"], name="unique-driver", platform="claude_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/drivers",
            json={"name": "unique-driver", "platform": "acp"},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_driver_invalid_platform_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/drivers",
            json={"name": "bad-driver", "platform": "invalid_platform"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_driver_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], name="get-test", platform="acp")
        resp = httpx.get(f"{ctx['url']}/api/drivers/{driver_id}", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["id"] == driver_id
        assert resp.json()["platform"] == "acp"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_driver_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/drivers/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_driver_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(
            ctx["db_url"], name="old-name", platform="claude_sdk"
        )
        resp = httpx.patch(
            f"{ctx['url']}/api/drivers/{driver_id}",
            json={"name": "new-name"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_driver_platform_rejected_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(
            ctx["db_url"], name="immut-test", platform="claude_sdk"
        )
        resp = httpx.patch(
            f"{ctx['url']}/api/drivers/{driver_id}",
            json={"platform": "acp"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_driver_config(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], name="config-test", platform="acp")
        resp = httpx.patch(
            f"{ctx['url']}/api/drivers/{driver_id}",
            json={"config": {"key": "value"}},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["config"]["key"] == "value"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_driver_no_agents(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], name="to-delete", platform="a2a")
        resp = httpx.delete(f"{ctx['url']}/api/drivers/{driver_id}", timeout=5)
        assert resp.status_code == 204

        # Verify it's gone
        resp2 = httpx.get(f"{ctx['url']}/api/drivers/{driver_id}", timeout=5)
        assert resp2.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_driver_with_agents_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        from tests.testhelpers import seed_test_agent

        # seed_test_agent auto-creates a driver, then we try to delete it
        seed_test_agent(
            ctx["db_url"], name="agent-blocking-delete", agent_type="copilot_sdk"
        )
        # Find the driver for copilot_sdk
        drivers_resp = httpx.get(f"{ctx['url']}/api/drivers", timeout=5)
        copilot_driver = [
            d for d in drivers_resp.json() if d["platform"] == "copilot_sdk"
        ]
        assert len(copilot_driver) == 1
        resp = httpx.delete(
            f"{ctx['url']}/api/drivers/{copilot_driver[0]['id']}", timeout=5
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_driver_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/drivers/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_driver_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_driver(ctx["db_url"], name="shape-test", platform="claude_sdk")
        resp = httpx.get(f"{ctx['url']}/api/drivers", timeout=5)
        driver = resp.json()[0]

        expected_fields = {
            "id",
            "name",
            "platform",
            "config",
            "created_at",
            "updated_at",
        }
        assert expected_fields.issubset(set(driver.keys()))
        assert isinstance(driver["config"], dict)
