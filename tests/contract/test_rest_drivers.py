# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    db_conn,
    scheduler_context,
    seed_test_agent,
    seed_test_driver,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_drivers_returns_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers", timeout=5)
        assert resp.status_code == 200
        drivers = resp.json()
        assert isinstance(drivers, list)
        assert len(drivers) == 4


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_driver_duplicate_platform_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/drivers",
            json={"name": "my-claude", "platform": "claude_sdk"},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_driver_name_collision_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/drivers",
            json={"name": "claude_sdk", "platform": "acp"},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_driver_invalid_platform_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/drivers",
            json={"name": "bad-driver", "platform": "invalid_platform"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("dead_platform", ["opencode_sdk", "a2a"])
def test_create_driver_dead_platforms_rejected(test_database, dead_platform):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/drivers",
            json={"name": f"test-{dead_platform}", "platform": dead_platform},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_driver_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], platform="acp")
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/{driver_id}", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["id"] == driver_id
        assert resp.json()["platform"] == "acp"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_driver_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_dead_driver_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT id FROM drivers WHERE platform = 'opencode_sdk'"
            ).fetchone()
        assert row is not None, "opencode_sdk driver should exist in DB from migration"
        dead_id = row[0]
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/{dead_id}", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_driver_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], platform="claude_sdk")
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/drivers/{driver_id}",
            json={"name": "new-name"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_driver_platform_rejected_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], platform="claude_sdk")
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/drivers/{driver_id}",
            json={"platform": "acp"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_driver_config(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = seed_test_driver(ctx["db_url"], platform="acp")
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/drivers/{driver_id}",
            json={"config": {"key": "value"}},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["config"]["key"] == "value"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_driver_no_agents(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers", timeout=5)
        acp_drivers = [d for d in resp.json() if d["platform"] == "acp"]
        assert len(acp_drivers) == 1
        acp_id = acp_drivers[0]["id"]

        resp = httpx.delete(f"{ctx['url']}/api/v1/drivers/{acp_id}", timeout=5)
        assert resp.status_code == 204

        resp2 = httpx.get(f"{ctx['url']}/api/v1/drivers/{acp_id}", timeout=5)
        assert resp2.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_driver_with_agents_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(
            ctx["db_url"], name="agent-blocking-delete", agent_type="copilot_sdk"
        )
        drivers_resp = httpx.get(f"{ctx['url']}/api/v1/drivers", timeout=5)
        copilot_driver = [
            d for d in drivers_resp.json() if d["platform"] == "copilot_sdk"
        ]
        assert len(copilot_driver) == 1
        resp = httpx.delete(
            f"{ctx['url']}/api/v1/drivers/{copilot_driver[0]['id']}", timeout=5
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_driver_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/v1/drivers/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_driver_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers", timeout=5)
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
