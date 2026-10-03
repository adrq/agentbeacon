# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)
from tests.wiki_helpers import make_share_tag


def _error_body(resp, status, error):
    assert resp.status_code == status, (status, resp.status_code, resp.text)
    assert not resp.headers["content-type"].startswith("application/problem+json"), (
        resp.text
    )
    body = resp.json()
    assert body["error"] == error, (error, body)
    for member in ("code", "title", "status", "detail"):
        assert member not in body, (member, body)
    return body


def _put(url, project_id, slug, **body):
    return httpx.put(
        f"{url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=body,
        timeout=15,
    )


def _patch(url, project_id, slug, **body):
    return httpx.patch(
        f"{url}/api/v1/projects/{project_id}/wiki/pages/{slug}",
        json=body,
        timeout=15,
    )


def _page(url, project_id, slug):
    resp = httpx.get(
        f"{url}/api/v1/projects/{project_id}/wiki/pages/{slug}", timeout=15
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@DUAL_BACKEND
def test_slug_conflict_keeps_its_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-slug")
        assert (
            _put(ctx["url"], project["id"], "page-a", title="A", body="one").status_code
            == 201
        )

        resp = _put(ctx["url"], project["id"], "page-a", title="A again", body="two")
        body = _error_body(resp, 409, "slug_exists")
        assert body["current_page"]["slug"] == "page-a"
        assert body["current_page"]["title"] == "A"


@DUAL_BACKEND
def test_revision_conflict_keeps_its_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-revision")
        assert (
            _put(
                ctx["url"], project["id"], "rev-page", title="Rev", body="one"
            ).status_code
            == 201
        )
        current = _page(ctx["url"], project["id"], "rev-page")
        assert (
            _patch(
                ctx["url"],
                project["id"],
                "rev-page",
                revision_number=current["revision_number"],
                edits=[{"old_string": "one", "new_string": "two"}],
            ).status_code
            == 200
        )
        advanced = _page(ctx["url"], project["id"], "rev-page")
        assert advanced["revision_number"] > current["revision_number"]

        resp = _patch(
            ctx["url"],
            project["id"],
            "rev-page",
            revision_number=current["revision_number"],
            edits=[{"old_string": "two", "new_string": "three"}],
        )
        body = _error_body(resp, 409, "revision_conflict")
        assert body["current_page"]["revision_number"] == advanced["revision_number"]


@DUAL_BACKEND
def test_edit_failed_keeps_its_error_body_with_the_edit_index(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-edit")
        assert (
            _put(
                ctx["url"], project["id"], "edit-page", title="Edit", body="hello"
            ).status_code
            == 201
        )
        current = _page(ctx["url"], project["id"], "edit-page")

        resp = _patch(
            ctx["url"],
            project["id"],
            "edit-page",
            revision_number=current["revision_number"],
            edits=[{"old_string": "not-present", "new_string": "x"}],
        )
        body = _error_body(resp, 422, "edit_failed")
        assert isinstance(body["reason"], str) and body["reason"]
        assert body["edit_index"] == 0
        assert body["current_page"]["slug"] == "edit-page"


@DUAL_BACKEND
def test_edit_failed_omits_the_index_when_the_result_is_at_fault(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-edit-noindex")
        assert (
            _put(
                ctx["url"], project["id"], "empty-page", title="Empty", body="content"
            ).status_code
            == 201
        )
        current = _page(ctx["url"], project["id"], "empty-page")

        resp = _patch(
            ctx["url"],
            project["id"],
            "empty-page",
            revision_number=current["revision_number"],
            edits=[{"old_string": "content", "new_string": ""}],
        )
        body = _error_body(resp, 422, "edit_failed")
        assert "edit_index" not in body
        assert body["reason"] == "empty_body"


@DUAL_BACKEND
def test_title_mismatch_keeps_its_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-title")
        assert (
            _put(
                ctx["url"], project["id"], "title-page", title="Real", body="one"
            ).status_code
            == 201
        )
        current = _page(ctx["url"], project["id"], "title-page")

        resp = _patch(
            ctx["url"],
            project["id"],
            "title-page",
            revision_number=current["revision_number"],
            title={"old": "Wrong", "new": "New"},
        )
        body = _error_body(resp, 422, "title_mismatch")
        assert body["current_page"]["title"] == "Real"


@DUAL_BACKEND
def test_tag_not_present_keeps_its_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-tag")
        assert (
            _put(
                ctx["url"], project["id"], "tag-page", title="Tag", body="one"
            ).status_code
            == 201
        )
        current = _page(ctx["url"], project["id"], "tag-page")

        resp = _patch(
            ctx["url"],
            project["id"],
            "tag-page",
            revision_number=current["revision_number"],
            remove_tags=["no-such-tag"],
        )
        body = _error_body(resp, 422, "tag_not_present")
        assert body["missing"] == ["no-such-tag"]
        assert body["current_page"]["slug"] == "tag-page"


@DUAL_BACKEND
def test_the_confirmation_flow_keeps_its_error_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        owner = create_project_via_api(ctx["url"], "wiki-error-body-confirm")
        peer = create_project_via_api(ctx["url"], "wiki-error-body-peer")
        make_share_tag(
            ctx["url"],
            "confirm-tag",
            {owner["id"]: "read_write", peer["id"]: "read"},
        )

        resp = _put(
            ctx["url"],
            owner["id"],
            "confirm-page",
            title="Confirm",
            body="one",
            tags=["confirm-tag"],
        )
        body = _error_body(resp, 409, "share_tag_requires_confirmation")
        assert body["publishes"] == [
            {
                "tag": "confirm-tag",
                "shares_with": [{"project": peer["slug"], "access": "read"}],
                "slug_conflicts": [],
            }
        ]
        assert body["warning"] == "all history travels with the page"
        assert body["remedy"] == 'retry with "acknowledge_share": true to publish'


@DUAL_BACKEND
def test_no_wiki_route_emits_a_problem_document(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "wiki-error-body-broad")

        probes = [
            httpx.get(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/missing",
                timeout=15,
            ),
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/missing",
                timeout=15,
            ),
            _put(ctx["url"], project["id"], "bad", title="", body="x"),
            httpx.get(f"{ctx['url']}/api/v1/wiki/search", timeout=15),
            httpx.get(
                f"{ctx['url']}/api/v1/projects/no-such-project/wiki/pages", timeout=15
            ),
        ]
        for resp in probes:
            if resp.status_code < 400:
                continue
            assert not resp.headers["content-type"].startswith(
                "application/problem+json"
            ), (resp.request.url, resp.text)
