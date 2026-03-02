"""Contract tests for wiki BM25 search (task H2)."""

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)


def create_wiki_project(base_url):
    return create_project_via_api(base_url, "wiki-search-project")


def put_page(base_url, project_id, slug, title, body):
    return httpx.put(
        f"{base_url}/api/projects/{project_id}/wiki/pages/{slug}",
        json={"title": title, "body": body},
        timeout=5,
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_relevance_title_boost(test_database):
    """Page with query term in title ranks above body-only match."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "k8s-setup",
            "Kubernetes Setup",
            "deployment guide",
        )
        put_page(
            ctx["url"],
            project["id"],
            "deploy-guide",
            "Deployment Guide",
            "kubernetes cluster config",
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=kubernetes",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Title match should rank first (2x boost)
        assert data[0]["slug"] == "k8s-setup"
        assert data[1]["slug"] == "deploy-guide"
        assert data[0]["score"] > 0
        assert data[1]["score"] > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_multi_term(test_database):
    """Multi-word query matches pages containing any term (OR semantics)."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "auth-page",
            "Auth Module",
            "authentication logic",
        )
        put_page(
            ctx["url"], project["id"], "token-page", "Token Service", "token generation"
        )
        put_page(
            ctx["url"], project["id"], "unrelated", "Database Setup", "postgres config"
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=auth+token",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        slugs = [d["slug"] for d in data]
        assert "auth-page" in slugs
        assert "token-page" in slugs
        assert "unrelated" not in slugs


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_deleted_page_excluded(test_database):
    """Deleted page does not appear in search results."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "temp-page", "Temporary", "ephemeral content"
        )

        httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/temp-page",
            timeout=5,
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=ephemeral",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_updated_content_indexed(test_database):
    """After update, search finds new content, not old."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "evolving", "Evolving Page", "alpha content"
        )

        # Update body
        httpx.put(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/evolving",
            json={
                "title": "Evolving Page",
                "body": "beta content",
                "revision_number": 1,
            },
            timeout=5,
        )

        # Search for new content
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=beta",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "evolving"

        # Search for old content should not find it
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=alpha",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_recreated_page(test_database):
    """After delete+recreate with same slug, search returns new content."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "phoenix", "Phoenix Page", "original content"
        )

        httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages/phoenix",
            timeout=5,
        )

        put_page(
            ctx["url"], project["id"], "phoenix", "Phoenix Page", "replacement content"
        )

        # New content found
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=replacement",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "phoenix"

        # Old content not found
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=original",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_empty_query_returns_all(test_database):
    """Empty or whitespace-only ?q= returns all pages (no search)."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "page-a", "Page A", "a")
        put_page(ctx["url"], project["id"], "page-b", "Page B", "b")
        put_page(ctx["url"], project["id"], "page-c", "Page C", "c")

        # Empty q
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=",
            timeout=5,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3

        # Whitespace-only q
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=%20",
            timeout=5,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_cross_project_isolation(test_database):
    """Search in project A does not return project B results."""
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "project-alpha")
        project_b = create_project_via_api(ctx["url"], "project-beta")

        put_page(
            ctx["url"], project_a["id"], "shared-term", "Shared Term", "unicorn data"
        )
        put_page(
            ctx["url"], project_b["id"], "also-shared", "Also Shared", "unicorn data"
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project_a['id']}/wiki/pages?q=unicorn",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "shared-term"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_special_characters(test_database):
    """Search handles special characters gracefully (no HTTP 500)."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "cpp-guide",
            "C++ Programming",
            "templates and classes",
        )

        # QueryParser lenient mode should not error on special chars
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=C%2B%2B",
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_case_insensitive(test_database):
    """BM25 search is case-insensitive."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "pg-migration",
            "PostgreSQL Migration",
            "database migration steps",
        )

        # Lowercase
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=postgresql",
            timeout=5,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["slug"] == "pg-migration"

        # Uppercase
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=POSTGRESQL",
            timeout=5,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["slug"] == "pg-migration"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_results_have_score(test_database):
    """Search results include BM25 score field."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "scored-page",
            "Scored Page",
            "searchable content",
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages?q=searchable",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "score" in data[0]
        assert data[0]["score"] > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_no_score_in_list(test_database):
    """List without ?q= does not include score field."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "plain-page", "Plain Page", "just content")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/pages",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "score" not in data[0]
