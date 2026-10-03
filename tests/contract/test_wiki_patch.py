# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    return create_project_via_api(base_url, "wiki-patch-project")


def put_page(base_url, project_id, slug, title, body, summary=None, tags=None):
    payload = {"title": title, "body": body}
    if summary is not None:
        payload["summary"] = summary
    if tags is not None:
        payload["tags"] = tags
    return httpx.put(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        timeout=5,
    )


def patch_page(
    base_url, project_id, slug, edits, revision_number, summary=None, headers=None
):
    payload = {"edits": edits, "revision_number": revision_number}
    if summary is not None:
        payload["summary"] = summary
    return httpx.patch(
        f"{base_url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=payload,
        headers=headers,
        timeout=5,
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_single_edit_updates_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "p1", "Title", "Hello world")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "p1",
            [{"old_string": "world", "new_string": "earth"}],
            1,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["body"] == "Hello earth"
        assert data["revision_number"] == 2
        assert data["title"] == "Title"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_multiple_sequential_edits(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "p2", "Title", "A then B")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "p2",
            [
                {"old_string": "A", "new_string": "C"},
                {"old_string": "C then", "new_string": "D and"},
            ],
            1,
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "D and B"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_replace_all(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "p3", "Title", "aXbXcX")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "p3",
            [{"old_string": "X", "new_string": "Y", "replace_all": True}],
            1,
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "aYbYcY"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_creates_single_revision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "p4", "Title", "AAA BBB CCC")
        patch_page(
            ctx["url"],
            project["id"],
            "p4",
            [
                {"old_string": "AAA", "new_string": "aaa"},
                {"old_string": "BBB", "new_string": "bbb"},
                {"old_string": "CCC", "new_string": "ccc"},
            ],
            1,
        )
        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/p4/revisions",
            timeout=5,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_preserves_title_and_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(
            ctx["url"],
            project["id"],
            "p5",
            "My Title",
            "body text",
            tags=["tag1", "tag2"],
        )
        patch_page(
            ctx["url"],
            project["id"],
            "p5",
            [{"old_string": "body", "new_string": "new"}],
            1,
        )
        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/p5",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My Title"
        assert sorted(data["tags"]) == ["tag1", "tag2"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_summary_recorded_on_revision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "p6", "Title", "old content")
        patch_page(
            ctx["url"],
            project["id"],
            "p6",
            [{"old_string": "old", "new_string": "new"}],
            1,
            summary="Fixed typo",
        )
        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/p6/revisions/1",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["summary"] == "Fixed typo"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_with_bearer_sets_updated_by(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        agent_id = seed_test_agent(ctx["db_url"], name="patch-agent")
        _, session_id = create_execution_via_api(
            ctx["url"],
            agent_id,
            "test",
            project_id=project["id"],
        )
        put_page(ctx["url"], project["id"], "p7", "Title", "body")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "p7",
            [{"old_string": "body", "new_string": "updated"}],
            1,
            headers={"Authorization": f"Bearer {session_id}"},
        )
        assert resp.status_code == 200
        assert resp.json()["updated_by"] == session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_empty_new_string_deletes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "p8", "Title", "remove this part")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "p8",
            [{"old_string": " this", "new_string": ""}],
            1,
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "remove part"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_wrong_revision_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "cas1", "Title", "body v1")
        patch_page(
            ctx["url"],
            project["id"],
            "cas1",
            [{"old_string": "body v1", "new_string": "body v2"}],
            1,
        )
        resp = patch_page(
            ctx["url"],
            project["id"],
            "cas1",
            [{"old_string": "body", "new_string": "x"}],
            1,
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "revision_conflict"
        assert data["current_page"]["body"] == "body v2"
        assert data["current_page"]["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_nonexistent_page_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = patch_page(
            ctx["url"],
            project["id"],
            "no-such-page",
            [{"old_string": "x", "new_string": "y"}],
            1,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_soft_deleted_page_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "del1", "Title", "body")
        httpx.delete(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/del1",
            timeout=5,
        )
        resp = patch_page(
            ctx["url"],
            project["id"],
            "del1",
            [{"old_string": "body", "new_string": "x"}],
            1,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_old_string_not_found_returns_422(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "e1", "Title", "hello world")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "e1",
            [{"old_string": "nonexistent", "new_string": "x"}],
            1,
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] == "edit_failed"
        assert data["reason"] == "not_found"
        assert data["edit_index"] == 0
        assert data["current_page"]["body"] == "hello world"
        get_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/e1",
            timeout=5,
        )
        assert get_resp.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_multiple_matches_without_replace_all_returns_422(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "e2", "Title", "foo bar foo")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "e2",
            [{"old_string": "foo", "new_string": "baz"}],
            1,
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["reason"] == "multiple_matches"
        assert data["current_page"]["body"] == "foo bar foo"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_overlapping_matches_returns_422(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "e3", "Title", "aaa")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "e3",
            [{"old_string": "aa", "new_string": "x"}],
            1,
        )
        assert resp.status_code == 422
        assert resp.json()["reason"] == "multiple_matches"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_atomicity_on_later_edit_failure(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "e4", "Title", "alpha beta")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "e4",
            [
                {"old_string": "alpha", "new_string": "gamma"},
                {"old_string": "nonexistent", "new_string": "x"},
            ],
            1,
        )
        assert resp.status_code == 422
        assert resp.json()["edit_index"] == 1
        get_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/e4",
            timeout=5,
        )
        assert get_resp.json()["body"] == "alpha beta"
        assert get_resp.json()["revision_number"] == 1
        rev_resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/e4/revisions",
            timeout=5,
        )
        assert len(rev_resp.json()) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_sequential_second_edit_sees_first(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "e5", "Title", "A remains")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "e5",
            [
                {"old_string": "A", "new_string": "B"},
                {"old_string": "B remains", "new_string": "done"},
            ],
            1,
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "done"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_empty_edits_array_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "v1", "Title", "body")
        resp = patch_page(ctx["url"], project["id"], "v1", [], 1)
        assert resp.status_code == 400
        assert "edits" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_missing_revision_number_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "v2", "Title", "body")
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/v2",
            json={"edits": [{"old_string": "a", "new_string": "b"}]},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_missing_edits_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/any-slug",
            json={},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_wrong_json_type_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/any-slug",
            json={"edits": [1, 2, 3], "revision_number": 1},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_malformed_json_body_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/any-slug",
            content=b"{",
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_empty_old_string_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "v6", "Title", "body")
        resp = patch_page(
            ctx["url"], project["id"], "v6", [{"old_string": "", "new_string": "x"}], 1
        )
        assert resp.status_code == 400
        assert "old_string" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_revision_number_zero_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "v7", "Title", "body")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "v7",
            [{"old_string": "body", "new_string": "x"}],
            0,
        )
        assert resp.status_code == 400
        assert "revision_number" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_edits_array_too_large_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "v8", "Title", "body")
        edits = [{"old_string": "body", "new_string": "body"}] * 51
        resp = patch_page(ctx["url"], project["id"], "v8", edits, 1)
        assert resp.status_code == 400
        assert "edits" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_invalid_slug_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = patch_page(
            ctx["url"],
            project["id"],
            "My Page",
            [{"old_string": "a", "new_string": "b"}],
            1,
        )
        assert resp.status_code == 400
        assert "slug" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_wrong_content_type_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/any-slug",
            content=b'{"edits": [{"old_string": "a", "new_string": "b"}], "revision_number": 1}',
            headers={"Content-Type": "text/plain"},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_without_auth_allowed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "auth1", "Title", "body")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "auth1",
            [{"old_string": "body", "new_string": "ok"}],
            1,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_unshared_foreign_page_is_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project_a = create_project_via_api(ctx["url"], "proj-a")
        project_b = create_project_via_api(ctx["url"], "proj-b")
        agent_id = seed_test_agent(ctx["db_url"], name="patch-agent")
        _, session_id = create_execution_via_api(
            ctx["url"],
            agent_id,
            "test",
            project_id=project_a["id"],
        )
        put_page(ctx["url"], project_b["id"], "auth2", "Title", "body")
        headers = {"Authorization": f"Bearer {session_id}"}
        edits = [{"old_string": "body", "new_string": "x"}]

        resp = patch_page(
            ctx["url"], project_b["id"], "auth2", edits, 1, headers=headers
        )
        assert resp.status_code == 404

        absent = patch_page(
            ctx["url"], project_b["id"], "no-such-page", edits, 1, headers=headers
        )
        assert absent.status_code == 404
        assert resp.json() == absent.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_malformed_bearer_returns_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "auth3", "Title", "body")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "auth3",
            [{"old_string": "body", "new_string": "x"}],
            1,
            headers={"Authorization": "NotBearer abc"},
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_unknown_session_id_returns_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "auth4", "Title", "body")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "auth4",
            [{"old_string": "body", "new_string": "x"}],
            1,
            headers={"Authorization": f"Bearer {uuid.uuid4()}"},
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_whole_body_rewrite_after_patch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "compat1", "Title", "body")
        patch_page(
            ctx["url"],
            project["id"],
            "compat1",
            [{"old_string": "body", "new_string": "patched"}],
            1,
        )
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/compat1",
            json={
                "revision_number": 2,
                "edits": [{"old_string": "patched", "new_string": "full rewrite"}],
                "title": {"old": "Title", "new": "New Title"},
            },
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 3
        assert resp.json()["body"] == "full rewrite"
        assert resp.json()["title"] == "New Title"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_after_patch_sees_new_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "compat2", "Title", "original")
        patch_page(
            ctx["url"],
            project["id"],
            "compat2",
            [{"old_string": "original", "new_string": "replaced"}],
            1,
        )
        resp = patch_page(
            ctx["url"],
            project["id"],
            "compat2",
            [{"old_string": "replaced", "new_string": "edited"}],
            2,
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "edited"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_uppercase_slug_normalized(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "my-page", "Title", "body")
        resp = patch_page(
            ctx["url"],
            project["id"],
            "MY-PAGE",
            [{"old_string": "body", "new_string": "ok"}],
            1,
        )
        assert resp.status_code == 200
        assert resp.json()["slug"] == "my-page"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_every_conflict_body_carries_the_pages_real_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        assert (
            put_page(
                ctx["url"],
                project["id"],
                "conflicted",
                "Title A",
                "hello world",
                tags=["design", "draft"],
            ).status_code
            == 201
        )

        def conflict(payload):
            return httpx.patch(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/conflicted",
                json=payload,
                timeout=5,
            )

        resp = conflict(
            {
                "revision_number": 99,
                "edits": [{"old_string": "hello", "new_string": "hi"}],
            }
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "revision_conflict"
        assert sorted(resp.json()["current_page"]["tags"]) == ["design", "draft"]

        resp = conflict(
            {"revision_number": 1, "title": {"old": "Not The Title", "new": "Title B"}}
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "title_mismatch"
        assert sorted(resp.json()["current_page"]["tags"]) == ["design", "draft"]

        resp = conflict({"revision_number": 1, "remove_tags": ["absent"]})
        assert resp.status_code == 422
        assert resp.json()["error"] == "tag_not_present"
        assert resp.json()["missing"] == ["absent"]
        assert sorted(resp.json()["current_page"]["tags"]) == ["design", "draft"]

        resp = conflict(
            {
                "revision_number": 1,
                "edits": [{"old_string": "not in the body", "new_string": "x"}],
            }
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "edit_failed"
        assert resp.json()["reason"] == "not_found"
        assert sorted(resp.json()["current_page"]["tags"]) == ["design", "draft"]

        page = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/conflicted",
            timeout=5,
        )
        assert page.status_code == 200
        assert page.json()["revision_number"] == 1
        assert sorted(page.json()["tags"]) == ["design", "draft"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_cannot_empty_a_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "doomed", "Doomed", "all of it")

        resp = patch_page(
            ctx["url"],
            project["id"],
            "doomed",
            [{"old_string": "all of it", "new_string": ""}],
            1,
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] == "edit_failed"
        assert data["reason"] == "empty_body"
        assert "edit_index" not in data

        page = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doomed", timeout=5
        )
        assert page.status_code == 200
        assert page.json()["body"] == "all of it"
        assert page.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_judges_emptiness_on_the_result_not_the_edits(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_wiki_project(ctx["url"])
        put_page(ctx["url"], project["id"], "stepwise", "Stepwise", "alpha beta")

        resp = patch_page(
            ctx["url"],
            project["id"],
            "stepwise",
            [
                {"old_string": "alpha ", "new_string": ""},
                {"old_string": "beta", "new_string": ""},
            ],
            1,
        )
        assert resp.status_code == 422
        assert resp.json()["reason"] == "empty_body"

        ok = patch_page(
            ctx["url"],
            project["id"],
            "stepwise",
            [{"old_string": "alpha ", "new_string": ""}],
            1,
        )
        assert ok.status_code == 200
        assert ok.json()["body"] == "beta"
