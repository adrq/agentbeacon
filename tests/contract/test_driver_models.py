# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os

import httpx
import pytest

from tests.testhelpers import scheduler_context

_CODEX_BIN_MOCK = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "mock_sdks", "codex-app-server-mock")
)

EXPECTED_CLAUDE_MODEL_IDS = [
    "opus",
    "sonnet",
    "haiku",
    "fable",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-8[1m]",
    "claude-opus-4-7",
    "claude-opus-4-7[1m]",
    "claude-opus-4-6",
    "claude-opus-4-6[1m]",
    "claude-sonnet-4-6",
    "claude-sonnet-4-6[1m]",
    "claude-haiku-4-5",
]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_claude_models_exact_ids(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/claude_sdk/models", timeout=5)
        assert resp.status_code == 200
        models = resp.json()
        ids = [m["id"] for m in models]
        assert ids == EXPECTED_CLAUDE_MODEL_IDS

        recommended_ids = [m["id"] for m in models if m.get("recommended")]
        assert recommended_ids == ["opus", "sonnet"]

        labels = {m["id"]: m["label"] for m in models}
        assert labels["fable"] == "Claude Fable (alias)"
        assert labels["claude-sonnet-5-5"] == "Claude Sonnet 5.5"
        assert labels["claude-sonnet-5"] == "Claude Sonnet 5"
        assert labels["claude-fable-5-1"] == "Claude Fable 5.1"
        assert labels["claude-fable-5"] == "Claude Fable 5"
        assert labels["claude-opus-5-5"] == "Claude Opus 5.5"
        assert labels["claude-opus-5"] == "Claude Opus 5"

        desc = {m["id"]: m.get("description") for m in models}
        assert desc["opus"] == "Alias — resolves to current Opus"
        assert desc["sonnet"] == "Alias — resolves to current Sonnet"
        assert desc["haiku"] == "Alias — resolves to current Haiku"
        assert desc["fable"] == "Alias — resolves to current Fable"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_claude_models_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/claude_sdk/models", timeout=5)
        assert resp.status_code == 200
        for model in resp.json():
            assert isinstance(model["id"], str)
            assert isinstance(model["label"], str)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_acp_models_returns_empty_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/acp/models", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_dead_platform_models_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/opencode_sdk/models", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_copilot_models_returns_200(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/copilot_sdk/models", timeout=15)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_models_returns_200(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/codex_sdk/models", timeout=15)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_models_happy_path(test_database):
    env = {"AGENTBEACON_CODEX_BIN": _CODEX_BIN_MOCK}
    with scheduler_context(db_url=test_database, env=env) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/drivers/codex_sdk/models", timeout=30)
        assert resp.status_code == 200
        models = resp.json()
        assert [m["id"] for m in models] == ["gpt-5.6-codex", "gpt-5.6"]
        assert [m["label"] for m in models] == ["GPT-5.6 Codex", "GPT-5.6"]
