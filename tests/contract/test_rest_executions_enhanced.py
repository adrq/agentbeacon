# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import set_execution_fields, set_session_fields


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_requires_project_id_or_cwd(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_branch_and_cwd_mutually_exclusive(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": tempfile.gettempdir(),
                "branch": "feature/test",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_branch_requires_project_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "branch": "feature/test",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_invalid_cwd_relative_path(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": "relative/path",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_invalid_cwd_nonexistent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": "/nonexistent/path/abc123",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_cwd(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test with cwd", cwd=tempfile.gettempdir()
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"][0]["cwd"] is not None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_project_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        project = create_project_via_api(ctx["url"], "my-project")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test with project", project_id=project["id"]
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["project_id"] == project["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_nonexistent_project_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "project_id": "nonexistent-project-id",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_branch_requires_git_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        project = create_project_via_api(ctx["url"], "non-git-project")
        assert project["is_git"] is False

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "project_id": project["id"],
                "branch": "feature/test",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_execution_from_awaiting_input(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "terminate me"
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()
        set_session_fields(
            ctx["db_url"], lead_sid, executor_state="idle", worker_id="w-idle"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_execution_already_terminal_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "terminate me"
        )

        set_execution_fields(
            ctx["db_url"], exec_id, desired="terminate", outcome="canceled"
        )
        set_session_fields(
            ctx["db_url"], session_id, desired="terminate", outcome="canceled"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_execution_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/nonexistent-id/terminate", timeout=5
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_events_empty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "test events")

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=5)
        assert resp.status_code == 200
        page = resp.json()
        assert isinstance(page["items"], list)
        assert page["has_more"] is False
        assert page["next_cursor"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_events_after_terminate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "terminate events")

        httpx.post(f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5)

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}/events", timeout=5)
        assert resp.status_code == 200
        events = resp.json()["items"]

        state_changes = [e for e in events if e["event_type"] == "state_change"]
        payloads = [e["payload"] for e in state_changes]

        assert any(
            p.get("outcome") is not None and e["session_id"] is not None
            for e, p in zip(state_changes, payloads)
        ), "missing session terminate state_change"
        assert any(
            p.get("outcome") is not None and e["session_id"] is None
            for e, p in zip(state_changes, payloads)
        ), "missing execution terminate state_change"

        for event in events:
            assert "id" in event
            assert "execution_id" in event
            assert "session_id" in event
            assert "event_type" in event
            assert "payload" in event
            assert "created_at" in event


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_events_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/nonexistent-id/events", timeout=5
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_context_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test context"}],
                "cwd": tempfile.gettempdir(),
                "context_id": "my-custom-context",
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution"]["context_id"] == "my-custom-context"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_auto_context_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "auto context")

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        data = resp.json()
        assert data["execution"]["context_id"] == exec_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "shape test"}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()

        assert "execution" in data
        assert "session_id" in data

        exec_fields = {
            "id",
            "desired",
            "outcome",
            "metadata",
            "created_at",
            "updated_at",
            "context_id",
        }
        assert exec_fields.issubset(set(data["execution"].keys()))

        assert isinstance(data["execution"]["metadata"], dict)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_detail_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "detail shape"
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        assert "execution" in data
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

        session = data["sessions"][0]
        session_fields = {
            "id",
            "execution_id",
            "agent_id",
            "desired",
            "executor_state",
            "outcome",
            "metadata",
            "created_at",
            "updated_at",
        }
        assert session_fields.issubset(set(session.keys()))


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_executions_with_offset(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        for i in range(3):
            create_execution_via_api(ctx["url"], agent_id, f"task {i}")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions", params={"limit": 2}, timeout=5
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions",
            params={"limit": 10, "offset": 2},
            timeout=5,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_executions_filter_by_project_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        project = create_project_via_api(ctx["url"], "filter-project")

        create_execution_via_api(
            ctx["url"], agent_id, "project task", project_id=project["id"]
        )

        create_execution_via_api(ctx["url"], agent_id, "cwd task")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions",
            params={"project_id": project["id"]},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["project_id"] == project["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_concurrent_warning(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        project = create_project_via_api(ctx["url"], "warn-project")

        resp1 = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "first"}],
                "project_id": project["id"],
            },
            timeout=5,
        )
        assert resp1.status_code == 201
        data1 = resp1.json()
        assert data1.get("warning") is None

        resp2 = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "second"}],
                "project_id": project["id"],
            },
            timeout=5,
        )
        assert resp2.status_code == 201
        data2 = resp2.json()
        assert data2["warning"] is not None
        assert "active" in data2["warning"].lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_empty_prompt_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "   "}],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_with_agent_id_populates_junction(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="junction-test")
        exec_id, _ = create_execution_via_api(
            ctx["url"], agent_id=agent_id, prompt="test", cwd="/tmp"
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT agent_id FROM execution_agents WHERE execution_id = ?",
                (exec_id,),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == agent_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_with_agent_ids_populates_junction(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent1 = seed_test_agent(
            ctx["db_url"], name="pool-agent-1", agent_type="claude_sdk"
        )
        agent2 = seed_test_agent(
            ctx["db_url"], name="pool-agent-2", agent_type="claude_sdk"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent1,
                "agent_ids": [agent1, agent2],
                "parts": [{"text": "multi-agent test"}],
                "cwd": "/tmp",
            },
            timeout=5,
        )
        assert resp.status_code == 201
        exec_id = resp.json()["execution"]["id"]

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT agent_id FROM execution_agents WHERE execution_id = ?",
                (exec_id,),
            ).fetchall()
        agent_ids = {r[0] for r in rows}
        assert agent_ids == {agent1, agent2}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_agents_returns_config_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent1 = seed_test_agent(
            ctx["db_url"], name="disc-agent-1", agent_type="claude_sdk"
        )
        agent2 = seed_test_agent(
            ctx["db_url"], name="disc-agent-2", agent_type="claude_sdk"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent1,
                "agent_ids": [agent1, agent2],
                "parts": [{"text": "discovery test"}],
                "cwd": "/tmp",
            },
            timeout=5,
        )
        assert resp.status_code == 201
        exec_id = resp.json()["execution"]["id"]

        disc_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/agents", timeout=5
        )
        assert disc_resp.status_code == 200
        entries = disc_resp.json()
        assert len(entries) == 2
        agent_ids = {e["agent_id"] for e in entries}
        assert agent_ids == {agent1, agent2}
        for entry in entries:
            assert "agent_id" in entry
            assert "name" in entry
            assert "agent_type" in entry


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_sessions_returns_session_discovery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent1 = seed_test_agent(
            ctx["db_url"], name="disc-agent-1", agent_type="claude_sdk"
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent1,
                "agent_ids": [agent1],
                "parts": [{"text": "discovery test"}],
                "cwd": "/tmp",
            },
            timeout=5,
        )
        assert resp.status_code == 201
        exec_id = resp.json()["execution"]["id"]
        session_id = resp.json()["session_id"]

        disc_resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/sessions", timeout=5
        )
        assert disc_resp.status_code == 200
        entries = disc_resp.json()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["session_id"] == session_id
        assert entry["agent_name"] == "disc-agent-1"
        assert "hierarchical_name" in entry
        assert entry["parent_name"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_requires_root_agent_id_and_agent_ids(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={"parts": [{"text": "no agent"}], "cwd": "/tmp"},
            timeout=5,
        )
        assert resp.status_code == 422

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "parts": [{"text": "no pool"}],
                "cwd": "/tmp",
            },
            timeout=5,
        )
        assert resp.status_code == 422

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "agent_ids": [agent_id],
                "parts": [{"text": "no root"}],
                "cwd": "/tmp",
            },
            timeout=5,
        )
        assert resp.status_code == 422


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_rejects_root_agent_not_in_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent1 = seed_test_agent(ctx["db_url"], name="root-agent")
        agent2 = seed_test_agent(ctx["db_url"], name="pool-agent")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent1,
                "agent_ids": [agent2],
                "parts": [{"text": "root not in pool"}],
                "cwd": "/tmp",
            },
            timeout=5,
        )
        assert resp.status_code == 400
