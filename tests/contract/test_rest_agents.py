"""Contract tests for GET /api/agents endpoint."""

import httpx
import pytest

from tests.testhelpers import (
    scheduler_context,
    seed_test_agent,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_agents_empty_returns_empty_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_agents_returns_seeded_agents(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")

        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == agent_id
        assert data[0]["name"] == "claude-code"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_agents_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        agent = resp.json()[0]

        expected_fields = {
            "id",
            "name",
            "description",
            "agent_type",
            "enabled",
            "config",
            "sandbox_config",
            "created_at",
            "updated_at",
        }
        assert expected_fields.issubset(set(agent.keys()))
        assert isinstance(agent["enabled"], bool)
        assert isinstance(agent["config"], dict)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_agents_multiple_sorted_by_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="zulu")
        seed_test_agent(ctx["db_url"], name="alpha")

        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "alpha"
        assert data[1]["name"] == "zulu"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_agents_includes_disabled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="active-agent", enabled=True)
        seed_test_agent(ctx["db_url"], name="disabled-agent", enabled=False)

        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        data = resp.json()
        assert len(data) == 2

        disabled = [a for a in data if a["name"] == "disabled-agent"][0]
        assert disabled["enabled"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_agents_timestamps_are_rfc3339(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.get(f"{ctx['url']}/api/agents", timeout=5)
        agent = resp.json()[0]

        assert "T" in agent["created_at"]
        assert "T" in agent["updated_at"]
