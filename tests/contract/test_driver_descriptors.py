# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import scheduler_context


VALID_PLATFORMS = ["claude_sdk", "copilot_sdk", "codex_sdk", "acp"]

EXPECTED_FIELD_KEYS = {
    "claude_sdk": {"model", "max_turns", "max_budget_usd", "effort", "thinking"},
    "copilot_sdk": {"model", "reasoning_effort", "provider", "api_key_env"},
    "codex_sdk": {
        "model",
        "model_reasoning_effort",
        "command",
        "args",
        "timeout",
        "env",
        "approval_policy",
        "sandbox_policy",
        "persist_extended_history",
        "api_key_env",
    },
    "acp": {"command", "args", "timeout", "env"},
}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("platform", VALID_PLATFORMS)
def test_get_descriptor_returns_200(test_database, platform):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/{platform}/descriptor", timeout=5
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["platform"] == platform
        assert isinstance(data["label"], str)
        assert isinstance(data["schema"], dict)
        assert isinstance(data["fields"], list)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("platform", VALID_PLATFORMS)
def test_descriptor_fields_have_required_keys(test_database, platform):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/{platform}/descriptor", timeout=5
        )
        assert resp.status_code == 200
        for field in resp.json()["fields"]:
            assert "pointer" in field
            assert "label" in field
            assert "group" in field
            assert "widget" in field
            assert "storage" in field
            assert "secret" in field


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("platform", VALID_PLATFORMS)
def test_descriptor_exact_field_keys(test_database, platform):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/{platform}/descriptor", timeout=5
        )
        assert resp.status_code == 200
        field_keys = {f["pointer"].split("/")[-1] for f in resp.json()["fields"]}
        assert field_keys == EXPECTED_FIELD_KEYS[platform]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("platform", VALID_PLATFORMS)
def test_descriptor_schema_is_valid_json_schema(test_database, platform):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/{platform}/descriptor", timeout=5
        )
        assert resp.status_code == 200
        schema = resp.json()["schema"]
        assert schema.get("type") == "object"
        assert "properties" in schema


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_descriptor_unsupported_platform_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/opencode_sdk/descriptor", timeout=5
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_descriptor_bogus_platform_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/bogus/descriptor", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_claude_descriptor_has_effort_enum(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/claude_sdk/descriptor", timeout=5
        )
        schema = resp.json()["schema"]
        effort_enum = schema["properties"]["effort"]["enum"]
        assert effort_enum == ["low", "medium", "high", "xhigh", "max"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_copilot_descriptor_has_reasoning_effort_enum(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/drivers/copilot_sdk/descriptor", timeout=5
        )
        schema = resp.json()["schema"]
        re_enum = schema["properties"]["reasoning_effort"]["enum"]
        assert re_enum == ["low", "medium", "high", "xhigh"]
