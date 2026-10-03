# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Contract tests for POST /api/v1/executions and GET /api/v1/executions/{id}."""

import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_returns_execution_and_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth"
        )

        assert exec_id is not None
        assert session_id is not None
        assert len(exec_id) == 36
        assert len(session_id) == 36


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_nonexistent_agent_returns_400(test_database):
    """Nonexistent root_agent_id returns 400 (validation error), not 404."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": "nonexistent-id",
                "agent_ids": ["nonexistent-id"],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_disabled_agent_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="disabled-agent", enabled=False)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_execution_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/executions/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_initial_state(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test task"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution"]["desired"] == "run"
        assert data["execution"]["outcome"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_execution_includes_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth"
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        assert data["execution"]["id"] == exec_id
        assert data["execution"]["desired"] == "run"
        assert data["execution"]["outcome"] is None
        assert "sessions" in data
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == session_id
        assert data["sessions"][0]["agent_id"] == agent_id
