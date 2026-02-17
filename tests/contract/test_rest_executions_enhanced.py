"""Contract tests for enhanced execution endpoints: project_id, branch, cwd, cancel, events."""

import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)


# --- Validation tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_requires_project_id_or_cwd(test_database):
    """At least project_id or cwd is required."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_branch_and_cwd_mutually_exclusive(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test",
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
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test",
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
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test",
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
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test",
                "cwd": "/nonexistent/path/abc123",
            },
            timeout=5,
        )
        assert resp.status_code == 400


# --- CWD execution tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_cwd(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test with cwd", cwd=tempfile.gettempdir()
        )

        # Verify execution detail
        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["status"] == "submitted"
        assert data["sessions"][0]["cwd"] is not None


# --- Project-based execution tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_project_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        project = create_project_via_api(ctx["url"], "my-project")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test with project", project_id=project["id"]
        )

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["project_id"] == project["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_nonexistent_project_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test",
                "project_id": "nonexistent-project-id",
            },
            timeout=5,
        )
        assert resp.status_code == 400


# --- Branch tests (require git-backed project) ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_branch_requires_git_project(test_database):
    """Branch requires project to be git-backed."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        # Create project pointing at a non-git directory
        project = create_project_via_api(ctx["url"], "non-git-project")
        assert project["is_git"] is False

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test",
                "project_id": project["id"],
                "branch": "feature/test",
            },
            timeout=5,
        )
        assert resp.status_code == 400


# --- Cancel tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_execution_submitted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "cancel me")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["status"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_execution_already_terminal_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "cancel me")

        # Cancel first time
        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=5)
        assert resp.status_code == 200

        # Cancel again — should be 409
        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=5)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_execution_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/executions/nonexistent-id/cancel", timeout=5
        )
        assert resp.status_code == 404


# --- Events endpoint tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_events_empty(test_database):
    """New execution should have state_change event from creation."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "test events")

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}/events", timeout=5)
        assert resp.status_code == 200
        events = resp.json()
        assert isinstance(events, list)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_events_after_cancel(test_database):
    """Canceling should produce state_change events."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "cancel events")

        httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=5)

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}/events", timeout=5)
        assert resp.status_code == 200
        events = resp.json()

        state_changes = [e for e in events if e["event_type"] == "state_change"]

        # Verify required transitions exist (don't assert exact count — contract
        # doesn't constrain how many internal state_change events are emitted)
        payloads = [e["payload"] for e in state_changes]
        assert any(p.get("to") == "submitted" for p in payloads), (
            "missing creation state_change"
        )
        assert any(
            p.get("to") == "canceled" and e["session_id"] is not None
            for e, p in zip(state_changes, payloads)
        ), "missing session cancel state_change"
        assert any(
            p.get("to") == "canceled" and e["session_id"] is None
            for e, p in zip(state_changes, payloads)
        ), "missing execution cancel state_change"

        # Verify event response shape
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
            f"{ctx['url']}/api/executions/nonexistent-id/events", timeout=5
        )
        assert resp.status_code == 404


# --- Context ID tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_context_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "test context",
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
    """When context_id is omitted, it defaults to execution_id."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "auto context")

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        data = resp.json()
        assert data["execution"]["context_id"] == exec_id


# --- Input field tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_input_stored_as_prompt(test_database):
    """Input should be stored as the plain prompt string."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id, _ = create_execution_via_api(
            ctx["url"], agent_id, "my plain prompt text"
        )

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        data = resp.json()
        assert data["execution"]["input"] == "my plain prompt text"


# --- Response shape tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_response_shape(test_database):
    """Verify the nested create response shape."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "shape test",
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()

        # Top-level keys
        assert "execution" in data
        assert "session_id" in data

        # Execution fields
        exec_fields = {
            "id",
            "status",
            "input",
            "metadata",
            "created_at",
            "updated_at",
            "context_id",
        }
        assert exec_fields.issubset(set(data["execution"].keys()))

        assert isinstance(data["execution"]["metadata"], dict)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_detail_response_shape(test_database):
    """Verify the nested detail response shape."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "detail shape"
        )

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        assert "execution" in data
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

        # Session fields
        session = data["sessions"][0]
        session_fields = {
            "id",
            "execution_id",
            "agent_id",
            "status",
            "coordination_mode",
            "metadata",
            "created_at",
            "updated_at",
        }
        assert session_fields.issubset(set(session.keys()))


# --- List with offset ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_executions_with_offset(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        # Create 3 executions
        for i in range(3):
            create_execution_via_api(ctx["url"], agent_id, f"task {i}")

        # List with limit=2
        resp = httpx.get(f"{ctx['url']}/api/executions", params={"limit": 2}, timeout=5)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        # List with offset=2 limit=10
        resp = httpx.get(
            f"{ctx['url']}/api/executions",
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

        # Create execution with project
        create_execution_via_api(
            ctx["url"], agent_id, "project task", project_id=project["id"]
        )

        # Create execution without project (cwd only)
        create_execution_via_api(ctx["url"], agent_id, "cwd task")

        # Filter by project_id
        resp = httpx.get(
            f"{ctx['url']}/api/executions",
            params={"project_id": project["id"]},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["project_id"] == project["id"]


# --- Concurrent warning tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_concurrent_warning(test_database):
    """Second execution for same project should return a warning."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        project = create_project_via_api(ctx["url"], "warn-project")

        # First execution — no warning
        resp1 = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "first",
                "project_id": project["id"],
            },
            timeout=5,
        )
        assert resp1.status_code == 201
        data1 = resp1.json()
        assert data1.get("warning") is None

        # Second execution — should warn
        resp2 = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "second",
                "project_id": project["id"],
            },
            timeout=5,
        )
        assert resp2.status_code == 201
        data2 = resp2.json()
        assert data2["warning"] is not None
        assert "active" in data2["warning"].lower()


# --- Empty prompt test ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_empty_prompt_returns_400(test_database):
    """Empty prompt should return 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "agent_id": agent_id,
                "prompt": "   ",
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400
