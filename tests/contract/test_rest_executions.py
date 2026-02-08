"""Contract tests for POST /api/executions and GET /api/executions/{id} with sessions."""

import httpx

from tests.testhelpers import (
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)


def test_create_execution_returns_execution_and_session():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth"
        )

        assert exec_id is not None
        assert session_id is not None
        assert len(exec_id) == 36
        assert len(session_id) == 36


def test_create_execution_status_is_submitted():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test task"},
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "submitted"


def test_create_execution_nonexistent_agent_returns_404():
    with scheduler_context() as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": "nonexistent-id", "prompt": "test"},
            timeout=5,
        )
        assert resp.status_code == 404


def test_create_execution_disabled_agent_returns_400():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="disabled-agent", enabled=False)

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test"},
            timeout=5,
        )
        assert resp.status_code == 400


def test_get_execution_includes_sessions():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_path"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth"
        )

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        assert data["id"] == exec_id
        assert data["status"] == "submitted"
        assert "sessions" in data
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == session_id
        assert data["sessions"][0]["agent_id"] == agent_id


def test_get_execution_nonexistent_returns_404():
    with scheduler_context() as ctx:
        resp = httpx.get(f"{ctx['url']}/api/executions/nonexistent-id", timeout=5)
        assert resp.status_code == 404
