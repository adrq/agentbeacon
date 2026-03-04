"""Contract tests for wiki extras: tags, subscriptions, changes feed, export (task H3)."""

import time
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)


def create_wiki_project(base_url):
    return create_project_via_api(base_url, "wiki-extras-test")


def put_page(
    base_url,
    project_id,
    slug,
    title,
    body,
    revision_number=None,
    summary=None,
    tags=None,
):
    payload = {"title": title, "body": body}
    if revision_number is not None:
        payload["revision_number"] = revision_number
    if summary is not None:
        payload["summary"] = summary
    if tags is not None:
        payload["tags"] = tags
    return httpx.put(
        f"{base_url}/api/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


# --- Tags tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_tags_empty_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/tags", timeout=5
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_tags_with_counts(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "page-a", "Page A", "body", tags=["auth", "backend"])
        put_page(ctx["url"], pid, "page-b", "Page B", "body", tags=["auth"])

        resp = httpx.get(f"{ctx['url']}/api/projects/{pid}/wiki/tags", timeout=5)
        assert resp.status_code == 200
        tags = {t["name"]: t["page_count"] for t in resp.json()}
        assert tags["auth"] == 2
        assert tags["backend"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_with_tags_creates_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        resp = put_page(
            ctx["url"], pid, "tagged", "Tagged", "body", tags=["foo", "bar"]
        )
        assert resp.status_code == 201
        data = resp.json()
        assert sorted(data["tags"]) == ["bar", "foo"]

        # Verify via GET
        get_resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/pages/tagged", timeout=5
        )
        assert sorted(get_resp.json()["tags"]) == ["bar", "foo"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_updates_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "tagged", "Tagged", "body", tags=["old-tag"])
        resp = put_page(
            ctx["url"],
            pid,
            "tagged",
            "Tagged v2",
            "body v2",
            revision_number=1,
            tags=["new-tag"],
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["new-tag"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_page_without_tags_preserves_existing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "tagged", "Tagged", "body", tags=["keep-me"])
        # Update without tags field
        resp = put_page(
            ctx["url"], pid, "tagged", "Tagged v2", "body v2", revision_number=1
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["keep-me"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tags_scoped_to_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        p1 = create_project_via_api(ctx["url"], "proj-1")
        p2 = create_project_via_api(ctx["url"], "proj-2")
        put_page(ctx["url"], p1["id"], "page", "P", "b", tags=["shared"])
        put_page(ctx["url"], p2["id"], "page", "P", "b", tags=["shared"])

        tags1 = httpx.get(
            f"{ctx['url']}/api/projects/{p1['id']}/wiki/tags", timeout=5
        ).json()
        tags2 = httpx.get(
            f"{ctx['url']}/api/projects/{p2['id']}/wiki/tags", timeout=5
        ).json()
        assert len(tags1) == 1
        assert tags1[0]["page_count"] == 1
        assert len(tags2) == 1
        assert tags2[0]["page_count"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_deleted_page_excluded_from_tag_counts(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "del-me", "Del", "body", tags=["temp"])
        httpx.delete(f"{ctx['url']}/api/projects/{pid}/wiki/pages/del-me", timeout=5)
        tags = httpx.get(f"{ctx['url']}/api/projects/{pid}/wiki/tags", timeout=5).json()
        assert tags == []


# --- Subscriptions tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_page_subscription(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "page_slug": "my-page"},
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["subscriber"] == "agent-1"
        assert data["page_slug"] == "my-page"
        assert data.get("tag_name") is None
        assert "id" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_tag_subscription(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "tag_name": "auth"},
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["tag_name"] == "auth"
        assert data.get("page_slug") is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_requires_exactly_one_target(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        # Both
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "a", "page_slug": "x", "tag_name": "y"},
            timeout=5,
        )
        assert resp.status_code == 400

        # Neither
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "a"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_duplicate_returns_200(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        body = {"subscriber": "agent-1", "page_slug": "my-page"}
        resp1 = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json=body,
            timeout=5,
        )
        resp2 = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json=body,
            timeout=5,
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 200
        assert resp1.json()["id"] == resp2.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_empty_subscriber_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "  ", "page_slug": "x"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_empty_target_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        # Empty string page_slug
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "page_slug": ""},
            timeout=5,
        )
        assert resp.status_code == 400

        # Whitespace-only tag_name
        resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "tag_name": "  "},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_nonexistent_project_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/projects/nonexistent/wiki/subscriptions",
            json={"subscriber": "a", "page_slug": "x"},
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_subscriptions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "page_slug": "my-page"},
            timeout=5,
        )
        httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "tag_name": "auth"},
            timeout=5,
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions?subscriber=agent-1",
            timeout=5,
        )
        assert resp.status_code == 200
        subs = resp.json()
        assert len(subs) == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_subscription(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        create_resp = httpx.post(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions",
            json={"subscriber": "agent-1", "page_slug": "my-page"},
            timeout=5,
        )
        sub_id = create_resp.json()["id"]

        del_resp = httpx.delete(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions/{sub_id}",
            timeout=5,
        )
        assert del_resp.status_code == 204

        # Verify it's gone
        list_resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions?subscriber=agent-1",
            timeout=5,
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_nonexistent_subscription_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{pid}/wiki/subscriptions/{uuid.uuid4()}",
            timeout=5,
        )
        assert resp.status_code == 404


# --- Changes feed tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_returns_revisions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "evolving", "V1", "body1")
        put_page(
            ctx["url"],
            pid,
            "evolving",
            "V2",
            "body2",
            revision_number=1,
            summary="update1",
        )
        put_page(
            ctx["url"],
            pid,
            "evolving",
            "V3",
            "body3",
            revision_number=2,
            summary="update2",
        )

        resp = httpx.get(f"{ctx['url']}/api/projects/{pid}/wiki/changes", timeout=5)
        assert resp.status_code == 200
        changes = resp.json()
        # 2 revisions (the first PUT creates the page, no revision archived)
        assert len(changes) == 2
        # Newest first
        assert changes[0]["revision_number"] == 2
        assert changes[1]["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_since_filter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "page", "V1", "b1")
        put_page(ctx["url"], pid, "page", "V2", "b2", revision_number=1, summary="s1")

        # Delay to ensure distinct timestamps at second granularity
        time.sleep(1.1)
        put_page(ctx["url"], pid, "page", "V3", "b3", revision_number=2, summary="s2")

        # Get all changes to find timestamps
        all_changes = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/changes", timeout=5
        ).json()
        rev1_ts = [c for c in all_changes if c["revision_number"] == 1][0]["created_at"]
        rev2_ts = [c for c in all_changes if c["revision_number"] == 2][0]["created_at"]
        assert rev2_ts > rev1_ts, "sleep should produce different timestamps"

        # Use httpx params= for proper URL encoding (handles '+' in timezone)
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/changes",
            params={"since": rev2_ts},
            timeout=5,
        )
        assert resp.status_code == 200
        changes = resp.json()
        # Only revision 2 has created_at >= rev2_ts
        assert len(changes) == 1
        assert changes[0]["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_execution_id_filter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        agent_id = seed_test_agent(ctx["db_url"], name="wiki-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "wiki work", project_id=pid
        )

        # PUT with auth (session_id as bearer token sets created_by)
        headers = {"Authorization": f"Bearer {session_id}"}
        httpx.put(
            f"{ctx['url']}/api/projects/{pid}/wiki/pages/auth-page",
            json={"title": "Auth", "body": "body"},
            headers=headers,
            timeout=5,
        )
        httpx.put(
            f"{ctx['url']}/api/projects/{pid}/wiki/pages/auth-page",
            json={
                "title": "Auth v2",
                "body": "body2",
                "revision_number": 1,
                "summary": "up",
            },
            headers=headers,
            timeout=5,
        )

        # PUT without auth (different "execution")
        put_page(ctx["url"], pid, "anon-page", "Anon", "body")
        put_page(
            ctx["url"],
            pid,
            "anon-page",
            "Anon v2",
            "body2",
            revision_number=1,
            summary="up2",
        )

        # Filter by execution_id — only auth-page revision
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/changes?execution_id={exec_id}",
            timeout=5,
        )
        assert resp.status_code == 200
        changes = resp.json()
        assert len(changes) == 1
        assert changes[0]["slug"] == "auth-page"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_limit(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "page", "V1", "b1")
        put_page(ctx["url"], pid, "page", "V2", "b2", revision_number=1, summary="s1")
        put_page(ctx["url"], pid, "page", "V3", "b3", revision_number=2, summary="s2")

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{pid}/wiki/changes",
            params={"limit": 1},
            timeout=5,
        )
        assert resp.status_code == 200
        changes = resp.json()
        assert len(changes) == 1
        assert changes[0]["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_empty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/changes", timeout=5
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_invalid_since_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/changes",
            params={"since": "not-a-date"},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "since" in resp.json()["error"]


# --- Export tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_export_returns_all_pages(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "alpha", "Alpha", "a-body")
        put_page(ctx["url"], pid, "beta", "Beta", "b-body")
        put_page(ctx["url"], pid, "gamma", "Gamma", "g-body")

        resp = httpx.get(f"{ctx['url']}/api/projects/{pid}/wiki/export", timeout=5)
        assert resp.status_code == 200
        pages = resp.json()
        assert len(pages) == 3
        slugs = [p["slug"] for p in pages]
        assert slugs == ["alpha", "beta", "gamma"]  # sorted by slug


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_export_excludes_deleted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "keep", "Keep", "body")
        put_page(ctx["url"], pid, "delete-me", "Delete", "body")
        httpx.delete(f"{ctx['url']}/api/projects/{pid}/wiki/pages/delete-me", timeout=5)

        resp = httpx.get(f"{ctx['url']}/api/projects/{pid}/wiki/export", timeout=5)
        pages = resp.json()
        assert len(pages) == 1
        assert pages[0]["slug"] == "keep"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_export_empty_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/wiki/export", timeout=5
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_export_nonexistent_project_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/projects/nonexistent/wiki/export", timeout=5
        )
        assert resp.status_code == 404


# --- Tags on list endpoint ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_pages_includes_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        pid = project["id"]
        put_page(ctx["url"], pid, "tagged-list", "Tagged", "body", tags=["t1", "t2"])
        put_page(ctx["url"], pid, "untagged", "Untagged", "body")

        # DB path
        resp = httpx.get(f"{ctx['url']}/api/projects/{pid}/wiki/pages", timeout=5)
        assert resp.status_code == 200
        pages_by_slug = {p["slug"]: p for p in resp.json()}
        assert sorted(pages_by_slug["tagged-list"]["tags"]) == ["t1", "t2"]
        assert pages_by_slug["untagged"]["tags"] == []
