# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)
from tests.wiki_helpers import (
    wiki_search,
)


def create_wiki_project(base_url):
    return create_project_via_api(base_url, "wiki-test-project")


def put_page(base_url, project_id, slug, title, body, summary=None):
    payload = {"title": title, "body": body}
    if summary is not None:
        payload["summary"] = summary
    return httpx.put(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


def update_page(
    base_url,
    project_id,
    slug,
    revision_number,
    old_body,
    new_body,
    title=None,
    summary=None,
):
    payload = {
        "revision_number": revision_number,
        "edits": [{"old_string": old_body, "new_string": new_body}],
    }
    if title is not None:
        payload["title"] = title
    if summary is not None:
        payload["summary"] = summary
    return httpx.patch(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_creates_new_page(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "my-page", "My Page", "Hello wiki")
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "my-page"
        assert data["title"] == "My Page"
        assert data["body"] == "Hello wiki"
        assert data["revision_number"] == 1
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_page_by_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "my-page", "My Page", "Hello wiki")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/my-page",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My Page"
        assert data["body"] == "Hello wiki"
        assert data["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_page_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/nonexistent",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_pages_returns_created_pages(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "page-a", "Page A", "a")
        put_page(ctx["url"], project["id"], "page-b", "Page B", "b")
        put_page(ctx["url"], project["id"], "page-c", "Page C", "c")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        for item in data:
            assert "slug" in item
            assert "title" in item
            assert "revision_number" in item
            assert "updated_at" in item
            assert "body" not in item


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_page_updates_existing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "my-page", "Original", "old body")

        resp = update_page(
            ctx["url"],
            project["id"],
            "my-page",
            1,
            "old body",
            "new body",
            title={"old": "Original", "new": "Updated"},
            summary="Changed title and body",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated"
        assert data["body"] == "new body"
        assert data["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_page(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "to-delete", "Delete Me", "bye")

        resp = httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/to-delete",
            timeout=5,
        )
        assert resp.status_code == 204

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/to-delete",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_page_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/nonexistent",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_page_revision_conflict(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "occ-page", "v1", "body v1")
        update_page(ctx["url"], project["id"], "occ-page", 1, "body v1", "body v2")

        resp = update_page(ctx["url"], project["id"], "occ-page", 1, "body v2", "stale")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "revision_conflict"
        assert data["current_page"]["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_slug_collision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "collision", "Original", "body")

        resp = put_page(ctx["url"], project["id"], "collision", "Duplicate", "dup")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "slug_exists"
        assert "current_page" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_page_update_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = update_page(ctx["url"], project["id"], "ghost", 1, "body", "new")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_after_delete_recreates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        create_resp = put_page(
            ctx["url"], project["id"], "reuse-slug", "First", "first"
        )
        assert create_resp.status_code == 201
        original_id = create_resp.json()["id"]

        httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/reuse-slug",
            timeout=5,
        )

        resp = put_page(ctx["url"], project["id"], "reuse-slug", "Second", "second")
        assert resp.status_code == 201
        data = resp.json()
        assert data["revision_number"] == 1
        assert data["id"] != original_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revisions_created_on_update(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "rev-page", "v1", "body v1")
        update_page(
            ctx["url"],
            project["id"],
            "rev-page",
            1,
            "body v1",
            "body v2",
            title={"old": "v1", "new": "v2"},
        )
        update_page(
            ctx["url"],
            project["id"],
            "rev-page",
            2,
            "body v2",
            "body v3",
            title={"old": "v2", "new": "v3"},
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/rev-page/revisions",
            timeout=5,
        )
        assert resp.status_code == 200
        revisions = resp.json()
        assert len(revisions) == 2
        assert revisions[0]["revision_number"] == 2
        assert revisions[1]["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revisions_contain_old_content(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "hist-page", "v1", "body v1")
        update_page(
            ctx["url"],
            project["id"],
            "hist-page",
            1,
            "body v1",
            "body v2",
            title={"old": "v1", "new": "v2"},
        )
        update_page(
            ctx["url"],
            project["id"],
            "hist-page",
            2,
            "body v2",
            "body v3",
            title={"old": "v2", "new": "v3"},
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/hist-page/revisions/1",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "v1"
        assert resp.json()["body"] == "body v1"

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/hist-page/revisions/2",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "v2"

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/hist-page",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "v3"
        assert resp.json()["revision_number"] == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_specific_revision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "rev-detail", "Original", "orig body")
        update_page(
            ctx["url"],
            project["id"],
            "rev-detail",
            1,
            "orig body",
            "new body",
            title={"old": "Original", "new": "Updated"},
            summary="Updated content",
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/rev-detail/revisions/1",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["revision_number"] == 1
        assert data["title"] == "Original"
        assert data["body"] == "orig body"
        assert data["summary"] == "Updated content"
        assert "created_at" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_revision_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "rev-404", "Page", "body")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/rev-404/revisions/99",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_invalid_slug_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])

        invalid_slugs = [
            "My Page",
            "page@home",
            "--leading",
            "trailing-",
            "double--hyphen",
        ]
        for slug in invalid_slugs:
            resp = put_page(ctx["url"], project["id"], slug, "Title", "body")
            assert resp.status_code == 400, (
                f"Expected 400 for slug '{slug}', got {resp.status_code}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_slug_too_long(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        long_slug = "a" * 201
        resp = put_page(ctx["url"], project["id"], long_slug, "Title", "body")
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_empty_title_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "valid-slug", "", "body")
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_normalized_to_lowercase(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "My-Page", "Title", "body")
        assert resp.status_code == 201
        assert resp.json()["slug"] == "my-page"

        get_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/MY-PAGE",
            timeout=5,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["slug"] == "my-page"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_same_slug_different_projects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "project-a")
        project_b = create_project_via_api(ctx["url"], "project-b")

        resp_a = put_page(
            ctx["url"], project_a["id"], "shared-slug", "Page A", "body a"
        )
        resp_b = put_page(
            ctx["url"], project_b["id"], "shared-slug", "Page B", "body b"
        )

        assert resp_a.status_code == 201
        assert resp_b.status_code == 201


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_pages_project_scoped(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "project-a")
        project_b = create_project_via_api(ctx["url"], "project-b")

        put_page(ctx["url"], project_a["id"], "page-a", "Page A", "a")
        put_page(ctx["url"], project_b["id"], "page-b", "Page B", "b")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project_a['id']}/wiki/pages",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "page-a"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_by_title(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "auth-setup", "Auth Setup", "body")
        put_page(ctx["url"], project["id"], "db-migration", "DB Migration", "body")

        resp = wiki_search(ctx["url"], q="auth", project=project["id"])
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "auth-setup"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_by_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "jwt-page", "Token Docs", "JWT token setup guide"
        )
        put_page(ctx["url"], project["id"], "other", "Other", "no match here")

        resp = wiki_search(ctx["url"], q="JWT", project=project["id"])
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "jwt-page"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_no_matches(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "some-page", "Some Page", "content")

        resp = wiki_search(ctx["url"], q="nonexistent", project=project["id"])
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_with_bearer_sets_created_by(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        agent_id = seed_test_agent(ctx["db_url"], name="wiki-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"],
            agent_id,
            "test",
            project_id=project["id"],
        )

        resp = httpx.put(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/auth-page",
            json={"title": "Auth Page", "body": "content"},
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created_by"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_without_auth_created_by_null(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "no-auth", "No Auth", "content")
        assert resp.status_code == 201
        data = resp.json()
        assert data.get("created_by") is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_wrong_project_forbidden(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "project-a")
        project_b = create_project_via_api(ctx["url"], "project-b")
        agent_id = seed_test_agent(ctx["db_url"], name="wiki-agent")
        _, session_id = create_execution_via_api(
            ctx["url"],
            agent_id,
            "test",
            project_id=project_a["id"],
        )

        resp = httpx.put(
            f"{ctx['url']}/api/v1/projects/{project_b['id']}/wiki/pages/cross-project",
            json={"title": "Cross", "body": "x"},
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=5,
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_empty_body_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "blank", "Blank Page", "")
        assert resp.status_code == 400
        assert resp.json()["error"] == "body must not be empty"

        assert (
            httpx.get(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/blank",
                timeout=5,
            ).status_code
            == 404
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revisions_for_deleted_page_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "del-rev", "Page", "body")
        update_page(
            ctx["url"],
            project["id"],
            "del-rev",
            1,
            "body",
            "new",
            title={"old": "Page", "new": "Updated"},
        )
        httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/del-rev",
            timeout=5,
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/del-rev/revisions",
            timeout=5,
        )
        assert resp.status_code == 404
