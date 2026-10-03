# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    with scheduler_context(db_url=test_database) as ctx:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.mkdir(os.path.join(tmpdir, ".git"))
            data = create_project_via_api(ctx["url"], "git-project", path=tmpdir)

            assert data["is_git"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_is_git_false(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        data = create_project_via_api(ctx["url"], "non-git-project")

        assert data["is_git"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_invalid_path_nonexistent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects",
            json={"name": "bad", "path": "/nonexistent/path/that/does/not/exist"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_invalid_path_relative(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects",
            json={"name": "bad", "path": "relative/path"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_duplicate_path_warning(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        path = tempfile.gettempdir()

        data1 = create_project_via_api(ctx["url"], "project-1", path=path)
        assert data1.get("warning") is None

        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects",
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

        resp = httpx.get(f"{ctx['url']}/api/v1/projects", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_projects_empty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/projects", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_project_by_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "my-project")

        resp = httpx.get(f"{ctx['url']}/api/v1/projects/{created['id']}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == created["id"]
        assert data["name"] == "my-project"
        assert isinstance(data["is_git"], bool)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_project_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/projects/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "original-name")

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{created['id']}",
            json={"name": "updated-name"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated-name"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_project_settings_replace(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "settings-test")

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{created['id']}",
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

        resp = httpx.delete(f"{ctx['url']}/api/v1/projects/{created['id']}", timeout=5)
        assert resp.status_code == 204

        resp = httpx.get(f"{ctx['url']}/api/v1/projects", timeout=5)
        assert len(resp.json()) == 0

        resp = httpx.get(f"{ctx['url']}/api/v1/projects/{created['id']}", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_project_nonexistent_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/v1/projects/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_project_seeds_agent_pool(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="my-agent")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects",
            json={
                "name": "agent-project",
                "path": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        pool_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project_id}/agents", timeout=5
        )
        assert pool_resp.status_code == 200
        pool = pool_resp.json()
        pool_agent_ids = [e["agent_id"] for e in pool]
        assert agent_id in pool_agent_ids


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_rejects_an_unrecognised_field(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects",
            json={
                "name": "not-created",
                "path": tempfile.gettempdir(),
                "is_git": False,
            },
            timeout=5,
        )
        assert resp.status_code == 422
        assert "unknown field `is_git`" in resp.text
        assert "expected one of `name`, `path`, `slug`" in resp.text

        listed = httpx.get(f"{ctx['url']}/api/v1/projects", timeout=5)
        assert listed.status_code == 200
        assert [p for p in listed.json() if p["name"] == "not-created"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_rejects_an_unrecognised_field(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "update-unknown-field")

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{created['id']}",
            json={"name": "renamed", "archived": True},
            timeout=5,
        )
        assert resp.status_code == 422
        assert "unknown field `archived`" in resp.text
        assert "expected one of `name`, `path`, `settings`, `slug`" in resp.text

        after = httpx.get(f"{ctx['url']}/api/v1/projects/{created['id']}", timeout=5)
        assert after.status_code == 200
        assert after.json()["name"] == "update-unknown-field"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_rejects_a_misspelled_slug_key(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project_via_api(ctx["url"], "slug-typo-project")
        original_slug = created["slug"]

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{created['id']}",
            json={"slugg": "intended-new-slug"},
            timeout=5,
        )
        assert resp.status_code == 422
        assert "unknown field `slugg`" in resp.text

        after = httpx.get(f"{ctx['url']}/api/v1/projects/{created['id']}", timeout=5)
        assert after.status_code == 200
        assert after.json()["slug"] == original_slug

        ok = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{created['id']}",
            json={"slug": "intended-new-slug"},
            timeout=5,
        )
        assert ok.status_code == 200
        assert ok.json()["slug"] == "intended-new-slug"
