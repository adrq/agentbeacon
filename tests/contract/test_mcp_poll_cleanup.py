"""Contract tests for MCP-poll cleanup (Task CL).

Verifies coordination_mode is no longer in session responses, and that
the sessions table no longer contains the coordination_mode column after
the 0011 migration.
"""

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_response_excludes_coordination_mode(test_database):
    """Session response no longer includes coordination_mode field."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cl-test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "cl test")

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        session = data["sessions"][0]
        assert "coordination_mode" not in session


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_list_excludes_coordination_mode(test_database):
    """GET /api/sessions response no longer includes coordination_mode field."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cl-test-agent")
        create_execution_via_api(ctx["url"], agent_id, "cl test")

        resp = httpx.get(f"{ctx['url']}/api/sessions", timeout=5)
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) > 0
        for s in sessions:
            assert "coordination_mode" not in s


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_coordination_mode_column_absent(test_database):
    """sessions table no longer has coordination_mode column after migration."""
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            if ctx["db_url"].startswith("sqlite:"):
                cursor = conn.execute("PRAGMA table_info(sessions)")
                columns = [row[1] for row in cursor.fetchall()]
            else:
                cursor = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s",
                    ("sessions",),
                )
                columns = [row[0] for row in cursor.fetchall()]

            assert "coordination_mode" not in columns


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_creation_works_after_column_drop(test_database):
    """Sessions can be created normally after coordination_mode column is dropped."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cl-test-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "cl post-drop test"
        )

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["status"] == "submitted"
        assert len(data["sessions"]) == 1
        session = data["sessions"][0]
        assert session["id"] == session_id
        assert session["status"] == "submitted"
