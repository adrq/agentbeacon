# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    db_conn,
    scheduler_context,
)


def _get_driver_id(ctx, platform):
    resp = httpx.get(f"{ctx['url']}/api/v1/drivers", timeout=5)
    drivers = [d for d in resp.json() if d["platform"] == platform]
    assert len(drivers) == 1, f"Expected 1 {platform} driver, got {len(drivers)}"
    return drivers[0]["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_invalid_model_type_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "claude_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "bad-agent",
                "driver_id": driver_id,
                "config": {"model": 123},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert (
            "model" in resp.json().get("error", "").lower()
            or "type" in resp.json().get("error", "").lower()
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_invalid_effort_enum_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "claude_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "bad-agent",
                "driver_id": driver_id,
                "config": {"effort": "nonsense"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_unknown_key_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "claude_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "bad-agent",
                "driver_id": driver_id,
                "config": {"unknown_field": "value"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_valid_claude_config_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "claude_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "good-agent",
                "driver_id": driver_id,
                "config": {"model": "claude-opus-4-6", "effort": "high"},
            },
            timeout=5,
        )
        assert resp.status_code == 201


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_valid_codex_config_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "codex_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "codex-agent",
                "driver_id": driver_id,
                "config": {"model": "gpt-5-codex"},
            },
            timeout=5,
        )
        assert resp.status_code == 201


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_empty_config_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "claude_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "minimal-agent",
                "driver_id": driver_id,
                "config": {},
            },
            timeout=5,
        )
        assert resp.status_code == 201


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_invalid_config_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "claude_sdk")
        create_resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "update-test-agent",
                "driver_id": driver_id,
                "config": {"model": "claude-opus-4-6"},
            },
            timeout=5,
        )
        assert create_resp.status_code == 201
        agent_id = create_resp.json()["id"]

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/agents/{agent_id}",
            json={"config": {"model": 999}},
            timeout=5,
        )
        assert resp.status_code == 400

        get_resp = httpx.get(f"{ctx['url']}/api/v1/agents/{agent_id}", timeout=5)
        assert get_resp.status_code == 200
        assert get_resp.json()["config"]["model"] == "claude-opus-4-6"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_copilot_invalid_reasoning_effort_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "copilot_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "bad-copilot",
                "driver_id": driver_id,
                "config": {"reasoning_effort": "nonsense"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_codex_reasoning_effort_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "codex_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "codex-effort-agent",
                "driver_id": driver_id,
                "config": {"model_reasoning_effort": "high"},
            },
            timeout=5,
        )
        assert resp.status_code == 201


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_codex_reasoning_effort_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "codex_sdk")
        create_resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "codex-update-effort-agent",
                "driver_id": driver_id,
                "config": {"model": "gpt-5-codex"},
            },
            timeout=5,
        )
        assert create_resp.status_code == 201
        agent_id = create_resp.json()["id"]

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/agents/{agent_id}",
            json={"config": {"model_reasoning_effort": "high"}},
            timeout=5,
        )
        assert resp.status_code == 200

        get_resp = httpx.get(f"{ctx['url']}/api/v1/agents/{agent_id}", timeout=5)
        assert get_resp.status_code == 200
        assert get_resp.json()["config"]["model_reasoning_effort"] == "high"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_codex_unknown_key_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "codex_sdk")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "codex-bad-agent",
                "driver_id": driver_id,
                "config": {"not_a_real_key": "x"},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "not_a_real_key" in resp.json().get("error", "")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_agent_codex_unknown_key_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        driver_id = _get_driver_id(ctx, "codex_sdk")
        create_resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "codex-update-bad-agent",
                "driver_id": driver_id,
                "config": {"model": "gpt-5-codex"},
            },
            timeout=5,
        )
        assert create_resp.status_code == 201
        agent_id = create_resp.json()["id"]

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/agents/{agent_id}",
            json={"config": {"not_a_real_key": "x"}},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "not_a_real_key" in resp.json().get("error", "")

        get_resp = httpx.get(f"{ctx['url']}/api/v1/agents/{agent_id}", timeout=5)
        assert get_resp.status_code == 200
        assert get_resp.json()["config"]["model"] == "gpt-5-codex"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_agent_with_legacy_driver_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT id FROM drivers WHERE platform = 'opencode_sdk'"
            ).fetchone()
        assert row is not None
        legacy_driver_id = row[0]

        resp = httpx.post(
            f"{ctx['url']}/api/v1/agents",
            json={
                "name": "legacy-agent",
                "driver_id": legacy_driver_id,
                "config": {},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "not supported" in resp.json().get("error", "").lower()
