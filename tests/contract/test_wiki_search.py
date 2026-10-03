# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)
from tests.wiki_helpers import (
    wiki_search,
)


def create_wiki_project(base_url):
    return create_project_via_api(base_url, "wiki-search-project")


def put_page(base_url, project_id, slug, title, body):
    return httpx.put(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json={"title": title, "body": body},
        timeout=5,
    )


def search(base_url, project_id, q):
    return wiki_search(base_url, q=q, project=project_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_relevance_title_boost(test_database):
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

        resp = search(ctx["url"], project["id"], "kubernetes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["slug"] == "k8s-setup"
        assert data[1]["slug"] == "deploy-guide"
        assert data[0]["score"] > 0
        assert data[1]["score"] > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_multi_term(test_database):
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

        resp = search(ctx["url"], project["id"], "auth+token")
        assert resp.status_code == 200
        data = resp.json()
        slugs = [d["slug"] for d in data]
        assert "auth-page" in slugs
        assert "token-page" in slugs
        assert "unrelated" not in slugs


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_deleted_page_excluded(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "temp-page", "Temporary", "ephemeral content"
        )

        httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/temp-page",
            timeout=5,
        )

        resp = search(ctx["url"], project["id"], "ephemeral")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_updated_content_indexed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "evolving", "Evolving Page", "alpha content"
        )

        httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/evolving",
            json={
                "revision_number": 1,
                "edits": [
                    {"old_string": "alpha content", "new_string": "beta content"}
                ],
            },
            timeout=5,
        )

        resp = search(ctx["url"], project["id"], "beta")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "evolving"

        resp = search(ctx["url"], project["id"], "alpha")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_recreated_page(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"], project["id"], "phoenix", "Phoenix Page", "original content"
        )

        httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/phoenix",
            timeout=5,
        )

        put_page(
            ctx["url"], project["id"], "phoenix", "Phoenix Page", "replacement content"
        )

        resp = search(ctx["url"], project["id"], "replacement")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "phoenix"

        resp = search(ctx["url"], project["id"], "original")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_listing_replaces_the_empty_query(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "page-a", "Page A", "a")
        put_page(ctx["url"], project["id"], "page-b", "Page B", "b")
        put_page(ctx["url"], project["id"], "page-c", "Page C", "c")

        for blank in ["", "   "]:
            resp = search(ctx["url"], project["id"], blank)
            assert resp.status_code == 400, f"q={blank!r}"
            assert resp.json()["error"] == "q is required"

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages", timeout=5
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_cross_project_isolation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "project-alpha")
        project_b = create_project_via_api(ctx["url"], "project-beta")

        put_page(
            ctx["url"], project_a["id"], "shared-term", "Shared Term", "unicorn data"
        )
        put_page(
            ctx["url"], project_b["id"], "also-shared", "Also Shared", "unicorn data"
        )

        resp = search(ctx["url"], project_a["id"], "unicorn")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "shared-term"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_special_characters(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "cpp-guide",
            "C++ Programming",
            "templates and classes",
        )
        put_page(
            ctx["url"], project["id"], "rust-guide", "Rust Guide", "ownership rules"
        )

        resp = search(ctx["url"], project["id"], "C++")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["cpp-guide"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_case_insensitive(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "pg-migration",
            "PostgreSQL Migration",
            "database migration steps",
        )

        resp = search(ctx["url"], project["id"], "postgresql")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["slug"] == "pg-migration"

        resp = search(ctx["url"], project["id"], "POSTGRESQL")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["slug"] == "pg-migration"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_results_have_score(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "scored-page",
            "Scored Page",
            "searchable content",
        )

        resp = search(ctx["url"], project["id"], "searchable")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "score" in data[0]
        assert data[0]["score"] > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_score_on_the_list_endpoint(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "plain-page", "Plain Page", "just content")

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "score" not in data[0]
