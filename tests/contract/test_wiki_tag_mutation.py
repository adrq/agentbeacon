# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)
from tests.wiki_helpers import (
    bearer,
    wiki_get,
    wiki_patch,
    wiki_put,
    wiki_replace_body,
    ERR_NOT_PAGE_OWNER,
    SHARE_TAG,
    build_world,
    materialize_page,
)


def tagged_project(ctx, slug="doc", tags=None):
    project = create_project_via_api(ctx["url"], "tag-mutation")
    resp = wiki_put(ctx["url"], project["id"], slug, "Doc", "body text", tags=tags)
    assert resp.status_code == 201, resp.text
    return project


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_tags_appends_without_replacing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft", "design"])

        resp = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=["spec"])
        assert resp.status_code == 200
        assert sorted(resp.json()["tags"]) == ["design", "draft", "spec"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_tags_removes_only_the_named_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft", "design"])

        resp = wiki_patch(ctx["url"], project["id"], "doc", 1, remove_tags=["draft"])
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["design"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_and_remove_in_one_request(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            add_tags=["published"],
            remove_tags=["draft"],
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["published"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_tags_tolerates_partial_overlap(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["spec"])

        resp = wiki_patch(
            ctx["url"], project["id"], "doc", 1, add_tags=["api-contract", "spec"]
        )
        assert resp.status_code == 200
        assert sorted(resp.json()["tags"]) == ["api-contract", "spec"]
        assert resp.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_tags_does_not_make_retries_safe(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["spec"])

        first = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=["design"])
        assert first.status_code == 200
        assert first.json()["revision_number"] == 2

        replay = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=["design"])
        assert replay.status_code == 409
        assert replay.json()["error"] == "revision_conflict"
        assert replay.json()["current_page"]["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_op_tag_change_does_not_bump_the_revision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["design", "draft"])

        resp = wiki_patch(
            ctx["url"], project["id"], "doc", 1, add_tags=["design", "draft"]
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 1
        assert sorted(resp.json()["tags"]) == ["design", "draft"]

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.json()["revision_number"] == 1

        changes = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/changes", timeout=5
        )
        assert changes.status_code == 200
        assert changes.json() == []

        resp = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=["published"])
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_op_tag_change_alongside_a_real_edit_still_bumps(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["design"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            edits=[{"old_string": "body text", "new_string": "edited text"}],
            add_tags=["design"],
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2
        assert resp.json()["body"] == "edited text"
        assert resp.json()["tags"] == ["design"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_tags_absent_tag_returns_422_naming_actual_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["design", "draft"])

        resp = wiki_patch(ctx["url"], project["id"], "doc", 1, remove_tags=["absent"])
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] == "tag_not_present"
        assert data["missing"] == ["absent"]
        assert sorted(data["current_page"]["tags"]) == ["design", "draft"]

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert sorted(page.json()["tags"]) == ["design", "draft"]
        assert page.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_tags_is_atomic_across_the_set(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["design"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            edits=[{"old_string": "body", "new_string": "changed"}],
            remove_tags=["design", "absent"],
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "tag_not_present"
        assert resp.json()["missing"] == ["absent"]

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.json()["tags"] == ["design"]
        assert page.json()["body"] == "body text"
        assert page.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_title_old_new_renames_when_old_matches(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            title={"old": "Doc", "new": "Renamed Doc"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed Doc"
        assert resp.json()["revision_number"] == 2
        assert resp.json()["body"] == "body text"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_title_mismatch_returns_422_naming_the_actual_title(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            title={"old": "Slightly Different", "new": "Renamed"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] == "title_mismatch"
        assert data["current_page"]["title"] == "Doc"

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.json()["title"] == "Doc"
        assert page.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_title_mismatch_rejects_the_whole_request(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["design"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            edits=[{"old_string": "body", "new_string": "changed"}],
            title={"old": "Wrong", "new": "Renamed"},
            add_tags=["published"],
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "title_mismatch"

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.json()["body"] == "body text"
        assert page.json()["tags"] == ["design"]
        assert page.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_title_requires_both_old_and_new(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        for payload in [
            {"old": "Doc"},
            {"new": "Renamed"},
            {},
            {"old": "Doc", "new": ""},
        ]:
            resp = httpx.patch(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doc",
                json={"revision_number": 1, "title": payload},
                timeout=5,
            )
            assert resp.status_code == 400, f"title {payload!r}"
            assert "title" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_title_as_a_bare_string_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doc",
            json={"revision_number": 1, "title": "Renamed"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_whole_body_replacement_is_a_single_edit(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_replace_body(
            ctx["url"], project["id"], "doc", 1, "body text", "completely new text"
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "completely new text"
        assert resp.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_whole_body_replacement_fails_when_the_old_body_is_stale(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_replace_body(
            ctx["url"], project["id"], "doc", 1, "what I think the body is", "new text"
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] == "edit_failed"
        assert data["reason"] == "not_found"
        assert data["edit_index"] == 0
        assert data["current_page"]["body"] == "body text"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tag_only_patch_bumps_revision_and_records_a_change(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            add_tags=["published"],
            summary="publish contract v2",
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2

        changes = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/changes", timeout=5
        )
        assert changes.status_code == 200
        assert len(changes.json()) == 1
        assert changes.json()[0]["revision_number"] == 1
        assert changes.json()[0]["summary"] == "publish contract v2"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_title_only_patch_bumps_revision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_patch(
            ctx["url"], project["id"], "doc", 1, title={"old": "Doc", "new": "Doc II"}
        )
        assert resp.status_code == 200
        assert resp.json()["revision_number"] == 2

        archived = httpx.get(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doc/revisions/1",
            timeout=5,
        )
        assert archived.status_code == 200
        assert archived.json()["title"] == "Doc"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tag_only_patch_preserves_body_and_title(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=["published"])
        assert resp.status_code == 200
        assert resp.json()["body"] == "body text"
        assert resp.json()["title"] == "Doc"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_revision_number_rejects_tag_change(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)
        assert (
            wiki_replace_body(
                ctx["url"], project["id"], "doc", 1, "body text", "second body"
            ).status_code
            == 200
        )

        resp = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=["published"])
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "revision_conflict"
        assert data["current_page"]["revision_number"] == 2

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.json()["tags"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_edits_is_optional(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doc",
            json={"revision_number": 1, "add_tags": ["published"]},
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_with_no_operation_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doc",
            json={"revision_number": 1},
            timeout=5,
        )
        assert resp.status_code == 400
        assert (
            resp.json()["error"]
            == "patch requires edits, title, add_tags or remove_tags"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_rejects_invalid_tag_names(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        for bad in ["", "   "]:
            resp = wiki_patch(ctx["url"], project["id"], "doc", 1, add_tags=[bad])
            assert resp.status_code == 400, f"tag {bad!r}"
            assert "tag" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_rejects_wrong_tag_payload_type(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx)

        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doc",
            json={"revision_number": 1, "add_tags": "published"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_removing_a_share_tag_needs_no_acknowledgment(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "retiring", tagged=True)

        resp = wiki_patch(
            ctx["url"], projects["owner"]["id"], "retiring", 2, remove_tags=[SHARE_TAG]
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cross_project_member_may_not_send_add_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "scoped", tagged=True)

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "scoped",
            2,
            edits=[{"old_string": "matrix body", "new_string": "member body"}],
            add_tags=["member-added"],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == ERR_NOT_PAGE_OWNER

        page = wiki_get(ctx["url"], projects["owner"]["id"], "scoped")
        assert page.json()["body"] == "matrix body text"
        assert page.json()["revision_number"] == 2

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "scoped",
            2,
            edits=[{"old_string": "matrix body", "new_string": "member body"}],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cross_project_member_may_retitle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "retitle", tagged=True)

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "retitle",
            2,
            title={"old": "Matrix Page", "new": "Member Retitled"},
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Member Retitled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_response_echoes_resulting_tag_set(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            add_tags=["published"],
            remove_tags=["draft"],
        )
        assert resp.status_code == 200
        echoed = resp.json()["tags"]

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert sorted(echoed) == sorted(page.json()["tags"])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_patch_tag_change_is_visible_to_members_immediately(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "late", "Late", "body text"
            ).status_code
            == 201
        )
        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "late",
                session_id=sessions["read_member"],
            ).status_code
            == 404
        )

        assert (
            wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "late",
                1,
                add_tags=[SHARE_TAG],
                acknowledge_share=True,
            ).status_code
            == 200
        )

        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "late",
                session_id=sessions["read_member"],
            ).status_code
            == 200
        )
        changes = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/changes",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert changes.status_code == 200
        assert [c["slug"] for c in changes.json()] == ["late"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tag_in_add_and_remove_is_rejected_when_the_page_carries_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft", "design"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            add_tags=["draft"],
            remove_tags=["draft"],
        )
        assert resp.status_code == 400
        data = resp.json()
        assert (
            data["error"]
            == "tags must not appear in both add_tags and remove_tags: draft"
        )

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.status_code == 200
        assert page.json()["revision_number"] == 1
        assert sorted(page.json()["tags"]) == ["design", "draft"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tag_in_add_and_remove_is_rejected_when_the_page_lacks_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["design"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            add_tags=["draft"],
            remove_tags=["draft"],
        )
        assert resp.status_code == 400
        data = resp.json()
        assert (
            data["error"]
            == "tags must not appear in both add_tags and remove_tags: draft"
        )
        assert "missing" not in data
        assert "current_page" not in data

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.status_code == 200
        assert page.json()["revision_number"] == 1
        assert page.json()["tags"] == ["design"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_overlap_is_detected_after_normalization(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft", "design"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "doc",
            1,
            add_tags=["  draft  ", "spec"],
            remove_tags=["draft"],
        )
        assert resp.status_code == 400
        data = resp.json()
        assert (
            data["error"]
            == "tags must not appear in both add_tags and remove_tags: draft"
        )

        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.status_code == 200
        assert sorted(page.json()["tags"]) == ["design", "draft"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_the_rejection_does_not_depend_on_the_page_existing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = tagged_project(ctx, tags=["draft"])

        resp = wiki_patch(
            ctx["url"],
            project["id"],
            "no-such-page",
            1,
            add_tags=["draft"],
            remove_tags=["draft"],
        )
        assert resp.status_code == 400
        assert (
            resp.json()["error"]
            == "tags must not appear in both add_tags and remove_tags: draft"
        )
