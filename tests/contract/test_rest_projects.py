"""Contract tests for /api/projects CRUD endpoints."""

import os
import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        data = create_project_via_api(ctx["url"], "my-project")

        assert data["name"] == "my-project"
        assert "id" in data
        assert len(data["id"]) == 36
        assert "created_at" in data
        assert "updated_at" in data
        assert isinstance(data["is_git"], bool)
        assert isinstance(data["settings"], dict)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_is_git_true(test_database):
    """A path containing .git should have is_git=true."""
    with scheduler_context(db_url=test_database) as ctx:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.mkdir(os.path.join(tmpdir, ".git"))
            data = create_project_via_api(ctx["url"], "git-project", path=tmpdir)

            assert data["is_git"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_is_git_false(test_database):
    """A temp dir should have is_git=false."""
    with scheduler_context(db_url=test_database) as ctx:
        data = create_project_via_api(ctx["url"], "non-git-project")

        assert data["is_git"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_invalid_path_nonexistent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/projects",
            json={"name": "bad", "path": "/nonexistent/path/that/does/not/exist"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_invalid_path_relative(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/projects",
            json={"name": "bad", "path": "relative/path"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_duplicate_path_warning(test_database):
    """Creating two projects with the same path should succeed with a warning."""
    with scheduler_context(db_url=test_database) as ctx:
        path = tempfile.gettempdir()

        # First project: no warning
        data1 = create_project_via_api(ctx["url"], "project-1", path=path)
        assert data1.get("warning") is None

        # Second project with same path: should include warning
        resp = httpx.post(
            f"{ctx['url']}/api/projects",
            json={"name": "project-2", "path": path},
            timeout=5,
        )
        assert resp.status_code == 201
        data2 = resp.json()
        assert data2.get("warning") is not None
        assert (
            "already" in data2["warning"].lower()
            or "duplicate" in data2["warning"].lower()
            or "existing" in data2["warning"].lower()
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_projects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_project_via_api(ctx["url"], "project-a")
        create_project_via_api(ctx["url"], "project-b")

        resp = httpx.get(f"{ctx['url']}/api/projects", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_projects_empty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/projects", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_project_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "my-project")

        resp = httpx.get(f"{ctx['url']}/api/projects/{created['id']}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == created["id"]
        assert data["name"] == "my-project"
        assert isinstance(data["is_git"], bool)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_project_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/projects/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "original-name")

        resp = httpx.patch(
            f"{ctx['url']}/api/projects/{created['id']}",
            json={"name": "updated-name"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated-name"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_project_settings_replace(test_database):
    """Settings are replaced entirely, not merged."""
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "settings-test")

        resp = httpx.patch(
            f"{ctx['url']}/api/projects/{created['id']}",
            json={"settings": {"key": "value"}},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"] == {"key": "value"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "to-delete")

        resp = httpx.delete(f"{ctx['url']}/api/projects/{created['id']}", timeout=5)
        assert resp.status_code == 204

        # Verify excluded from GET list
        resp = httpx.get(f"{ctx['url']}/api/projects", timeout=5)
        assert len(resp.json()) == 0

        # Verify excluded from GET by ID
        resp = httpx.get(f"{ctx['url']}/api/projects/{created['id']}", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_project_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/projects/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_with_default_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="my-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/projects",
            json={
                "name": "agent-project",
                "path": tempfile.gettempdir(),
                "default_agent_id": agent_id,
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["default_agent_id"] == agent_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_invalid_default_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/projects",
            json={
                "name": "bad-agent-project",
                "path": tempfile.gettempdir(),
                "default_agent_id": "nonexistent-agent",
            },
            timeout=5,
        )
        assert resp.status_code == 400
