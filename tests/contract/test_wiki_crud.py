"""Contract tests for wiki CRUD REST API (task H1)."""

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)


def create_wiki_project(base_url):
    """Create a project for wiki tests."""
    return create_project_via_api(base_url, "wiki-test-project")


def put_page(
    base_url, project_id, slug, title, body, revision_number=None, summary=None
):
    """PUT a wiki page, returning the raw response."""
    payload = {"title": title, "body": body}
    if revision_number is not None:
        payload["revision_number"] = revision_number
    if summary is not None:
        payload["summary"] = summary
    return httpx.put(
        f"{base_url}/api/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


# --- CRUD happy paths ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_creates_new_page(test_database):
    """PUT with no revision_number creates a new wiki page (201)."""
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
    """GET returns the created page with full content."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "my-page", "My Page", "Hello wiki")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/my-page",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My Page"
        assert data["body"] == "Hello wiki"
        assert data["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_page_not_found(test_database):
    """GET for nonexistent slug returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/nonexistent",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_pages_returns_created_pages(test_database):
    """GET list returns all non-deleted pages for the project."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "page-a", "Page A", "a")
        put_page(ctx["url"], project["id"], "page-b", "Page B", "b")
        put_page(ctx["url"], project["id"], "page-c", "Page C", "c")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        # List items should NOT include body
        for item in data:
            assert "slug" in item
            assert "title" in item
            assert "revision_number" in item
            assert "updated_at" in item
            assert "body" not in item


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_updates_existing(test_database):
    """PUT with matching revision_number updates page (200)."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "my-page", "Original", "old body")

        resp = put_page(
            ctx["url"],
            project["id"],
            "my-page",
            "Updated",
            "new body",
            revision_number=1,
            summary="Changed title and body",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated"
        assert data["body"] == "new body"
        assert data["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_page(test_database):
    """DELETE soft-deletes page, subsequent GET returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "to-delete", "Delete Me", "bye")

        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/to-delete",
            timeout=5,
        )
        assert resp.status_code == 204

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/to-delete",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_page_not_found(test_database):
    """DELETE on nonexistent page returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/nonexistent",
            timeout=5,
        )
        assert resp.status_code == 404


# --- OCC tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_revision_conflict(test_database):
    """PUT with wrong revision_number returns 409 with current page."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "occ-page", "v1", "body v1")
        put_page(
            ctx["url"],
            project["id"],
            "occ-page",
            "v2",
            "body v2",
            revision_number=1,
        )

        # Try to update with stale revision
        resp = put_page(
            ctx["url"],
            project["id"],
            "occ-page",
            "stale",
            "stale",
            revision_number=1,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "revision_conflict"
        assert data["current_page"]["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_slug_collision(test_database):
    """PUT create mode on existing slug returns 409 with existing page."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "collision", "Original", "body")

        # Try to create with same slug (no revision_number = create mode)
        resp = put_page(ctx["url"], project["id"], "collision", "Duplicate", "dup")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "slug_exists"
        assert "current_page" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_update_not_found(test_database):
    """PUT update mode on nonexistent page returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(
            ctx["url"],
            project["id"],
            "ghost",
            "Ghost",
            "body",
            revision_number=1,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_after_delete_recreates(test_database):
    """PUT create mode after soft delete creates new page with same slug."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        create_resp = put_page(
            ctx["url"], project["id"], "reuse-slug", "First", "first"
        )
        assert create_resp.status_code == 201
        original_id = create_resp.json()["id"]

        httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/reuse-slug",
            timeout=5,
        )

        resp = put_page(ctx["url"], project["id"], "reuse-slug", "Second", "second")
        assert resp.status_code == 201
        data = resp.json()
        assert data["revision_number"] == 1
        assert data["id"] != original_id


# --- Revision history tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revisions_created_on_update(test_database):
    """Updating a page creates revision entries."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "rev-page", "v1", "body v1")
        put_page(
            ctx["url"],
            project["id"],
            "rev-page",
            "v2",
            "body v2",
            revision_number=1,
        )
        put_page(
            ctx["url"],
            project["id"],
            "rev-page",
            "v3",
            "body v3",
            revision_number=2,
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/rev-page/revisions",
            timeout=5,
        )
        assert resp.status_code == 200
        revisions = resp.json()
        assert len(revisions) == 2
        # Newest first
        assert revisions[0]["revision_number"] == 2
        assert revisions[1]["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revisions_contain_old_content(test_database):
    """Each revision preserves the content from before the update."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "hist-page", "v1", "body v1")
        put_page(
            ctx["url"],
            project["id"],
            "hist-page",
            "v2",
            "body v2",
            revision_number=1,
        )
        put_page(
            ctx["url"],
            project["id"],
            "hist-page",
            "v3",
            "body v3",
            revision_number=2,
        )

        # Revision 1 has the original content
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/hist-page/revisions/1",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "v1"
        assert resp.json()["body"] == "body v1"

        # Revision 2 has v2 content
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/hist-page/revisions/2",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "v2"

        # Current page is v3
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/hist-page",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "v3"
        assert resp.json()["revision_number"] == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_specific_revision(test_database):
    """GET revision by number returns full content."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "rev-detail", "Original", "orig body")
        put_page(
            ctx["url"],
            project["id"],
            "rev-detail",
            "Updated",
            "new body",
            revision_number=1,
            summary="Updated content",
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/rev-detail/revisions/1",
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
    """GET nonexistent revision number returns 404."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "rev-404", "Page", "body")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/rev-404/revisions/99",
            timeout=5,
        )
        assert resp.status_code == 404


# --- Slug validation tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_invalid_slug_rejected(test_database):
    """PUT with invalid slug characters returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])

        invalid_slugs = [
            "My Page",  # spaces
            "page@home",  # special chars
            "--leading",  # leading hyphen
            "trailing-",  # trailing hyphen
            "double--hyphen",  # consecutive hyphens
        ]
        for slug in invalid_slugs:
            resp = put_page(ctx["url"], project["id"], slug, "Title", "body")
            assert resp.status_code == 400, (
                f"Expected 400 for slug '{slug}', got {resp.status_code}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_slug_too_long(test_database):
    """PUT with slug > 200 chars returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        long_slug = "a" * 201
        resp = put_page(ctx["url"], project["id"], long_slug, "Title", "body")
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_empty_title_rejected(test_database):
    """PUT with empty title returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "valid-slug", "", "body")
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_normalized_to_lowercase(test_database):
    """Uppercase slug in URL is normalized to lowercase for both create and read."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        # Create with uppercase slug — should be normalized
        resp = put_page(ctx["url"], project["id"], "My-Page", "Title", "body")
        assert resp.status_code == 201
        assert resp.json()["slug"] == "my-page"

        # Read with uppercase slug — should find the page
        get_resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/MY-PAGE",
            timeout=5,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["slug"] == "my-page"


# --- Cross-project isolation tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_same_slug_different_projects(test_database):
    """Same slug can exist in two different projects."""
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
    """Listing pages for project A does not include project B's pages."""
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "project-a")
        project_b = create_project_via_api(ctx["url"], "project-b")

        put_page(ctx["url"], project_a["id"], "page-a", "Page A", "a")
        put_page(ctx["url"], project_b["id"], "page-b", "Page B", "b")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project_a['id']}/wiki/pages",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "page-a"


# --- Search tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_by_title(test_database):
    """List with ?q= filters by title match."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "auth-setup", "Auth Setup", "body")
        put_page(ctx["url"], project["id"], "db-migration", "DB Migration", "body")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=auth",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "auth-setup"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_by_body(test_database):
    """List with ?q= filters by body match."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "jwt-page", "Token Docs", "JWT token setup guide"
        )
        put_page(ctx["url"], project["id"], "other", "Other", "no match here")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=JWT",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "jwt-page"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_no_matches(test_database):
    """List with ?q= returns empty array when no matches."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "some-page", "Some Page", "content")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=nonexistent",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json() == []


# --- Auth tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_with_bearer_sets_created_by(test_database):
    """PUT with valid Bearer token sets created_by to session_id."""
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
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/auth-page",
            json={"title": "Auth Page", "body": "content"},
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created_by"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_without_auth_created_by_null(test_database):
    """PUT without Bearer token sets created_by to null."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "no-auth", "No Auth", "content")
        assert resp.status_code == 201
        data = resp.json()
        assert data.get("created_by") is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_wrong_project_forbidden(test_database):
    """PUT with Bearer token for session in different project returns 403."""
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

        # Use project A's session to write to project B
        resp = httpx.put(
            f"{ctx['url']}/api/projects/{project_b['id']}/wiki/pages/cross-project",
            json={"title": "Cross", "body": "x"},
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=5,
        )
        assert resp.status_code == 403


# --- Edge case tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_empty_body_allowed(test_database):
    """PUT with empty body creates page (blank page is valid)."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = put_page(ctx["url"], project["id"], "blank", "Blank Page", "")
        assert resp.status_code == 201
        assert resp.json()["body"] == ""


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revisions_for_deleted_page_404(test_database):
    """Revisions endpoint returns 404 for deleted page."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "del-rev", "Page", "body")
        put_page(
            ctx["url"],
            project["id"],
            "del-rev",
            "Updated",
            "new",
            revision_number=1,
        )
        httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/del-rev",
            timeout=5,
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/del-rev/revisions",
            timeout=5,
        )
        assert resp.status_code == 404
