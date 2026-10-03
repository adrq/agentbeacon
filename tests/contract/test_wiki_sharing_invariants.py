# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.wiki_helpers import (
    admit_member,
    bearer,
    membership_rows,
    revoke_member,
    revoke_membership_in_db,
    set_member_access,
    tag_id_for,
    untag_page_in_db,
    wiki_get,
    wiki_patch,
    wiki_put,
    wiki_search,
    ERR_CROSS_PROJECT_CREATE,
    ERR_NOT_PAGE_OWNER,
    SHARE_TAG,
    build_world,
    materialize_page,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_authorization_always_reads_live_database_state(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "live-state", tagged=True)

        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "live-state",
                session_id=sessions["read_member"],
            ).status_code
            == 200
        )

        untag_page_in_db(
            ctx["db_url"], projects["owner"]["id"], "live-state", SHARE_TAG
        )

        for suffix in ["", "/revisions", "/revisions/1"]:
            resp = httpx.get(
                f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
                f"/wiki/pages/live-state{suffix}",
                headers=bearer(sessions["read_member"]),
                timeout=5,
            )
            assert resp.status_code == 404, f"path {suffix!r} served a stale decision"

        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/pages",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert listing.status_code == 200
        assert listing.json() == []

        searched = wiki_search(
            ctx["url"], session_id=sessions["read_member"], q="matrix"
        )
        assert searched.status_code == 200
        assert [r["slug"] for r in searched.json()] == ["live-state"]
        assert "body" not in searched.json()[0]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_scope_is_rebuilt_from_live_membership(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(
            ctx["url"], projects["owner"]["id"], "stale-member", tagged=True
        )

        found = wiki_search(ctx["url"], session_id=sessions["read_member"], q="matrix")
        assert [r["slug"] for r in found.json()] == ["stale-member"]

        revoke_membership_in_db(ctx["db_url"], SHARE_TAG, projects["read_member"]["id"])

        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "stale-member",
                session_id=sessions["read_member"],
            ).status_code
            == 404
        )
        searched = wiki_search(
            ctx["url"], session_id=sessions["read_member"], q="matrix"
        )
        assert searched.status_code == 200
        assert searched.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_only_owner_changes_tags_or_deletes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "owned", tagged=True)

        add = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "owned",
            2,
            add_tags=["member-added"],
            session_id=sessions["write_member"],
        )
        assert add.status_code == 403
        assert add.json()["error"] == ERR_NOT_PAGE_OWNER

        remove = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "owned",
            2,
            remove_tags=[SHARE_TAG],
            session_id=sessions["write_member"],
        )
        assert remove.status_code == 403
        assert remove.json()["error"] == ERR_NOT_PAGE_OWNER

        destroy = httpx.delete(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}/wiki/pages/owned",
            headers=bearer(sessions["write_member"]),
            timeout=5,
        )
        assert destroy.status_code == 403
        assert destroy.json()["error"] == ERR_NOT_PAGE_OWNER

        page = wiki_get(ctx["url"], projects["owner"]["id"], "owned")
        assert page.status_code == 200
        assert page.json()["tags"] == [SHARE_TAG]
        assert page.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_members_create_pages_only_in_their_own_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)

        resp = wiki_put(
            ctx["url"],
            projects["owner"]["id"],
            "planted",
            "Planted",
            "body",
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == ERR_CROSS_PROJECT_CREATE
        assert (
            wiki_get(ctx["url"], projects["owner"]["id"], "planted").status_code == 404
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_publication_is_never_implicit(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="implicit-agent")
        owner = create_project_via_api(ctx["url"], "implicit-owner")
        peer = create_project_via_api(ctx["url"], "implicit-peer")
        create_execution_via_api(ctx["url"], agent_id, "x", project_id=owner["id"])

        assert (
            wiki_put(
                ctx["url"], owner["id"], "pre-existing", "Doc", "body", tags=["cutting"]
            ).status_code
            == 201
        )
        tag_id = tag_id_for(ctx["url"], "cutting")
        assert (
            admit_member(ctx["url"], tag_id, owner["id"], "read_write").status_code
            == 201
        )

        resp = admit_member(ctx["url"], tag_id, peer["id"], "read")
        assert resp.status_code == 409
        assert resp.json()["error"] == "membership_requires_confirmation"
        assert (
            admit_member(
                ctx["url"], tag_id, peer["id"], "read", acknowledge_share=True
            ).status_code
            == 201
        )

        assert (
            wiki_put(ctx["url"], owner["id"], "later", "Doc", "body").status_code == 201
        )
        resp = wiki_patch(ctx["url"], owner["id"], "later", 1, add_tags=["cutting"])
        assert resp.status_code == 409
        assert resp.json()["error"] == "share_tag_requires_confirmation"

        resp = wiki_put(
            ctx["url"], owner["id"], "newborn", "Doc", "body", tags=["cutting"]
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "share_tag_requires_confirmation"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_every_membership_read_filters_revoked_at_is_null(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "filtered", tagged=True)
        member_project = projects["read_member"]["id"]
        owner_project = projects["owner"]["id"]
        headers = bearer(sessions["read_member"])

        assert revoke_member(ctx["url"], tag_id, member_project).status_code == 204

        rows = membership_rows(ctx["db_url"], SHARE_TAG, member_project)
        assert len(rows) == 1
        assert rows[0][2] is not None

        for path, params in [
            (f"/api/v1/projects/{owner_project}/wiki/pages/filtered", {}),
            (f"/api/v1/projects/{owner_project}/wiki/pages/filtered/revisions", {}),
            (f"/api/v1/projects/{owner_project}/wiki/pages/filtered/revisions/1", {}),
        ]:
            resp = httpx.get(
                f"{ctx['url']}{path}", params=params, headers=headers, timeout=5
            )
            assert resp.status_code == 404, path

        for path in ["pages", "changes"]:
            resp = httpx.get(
                f"{ctx['url']}/api/v1/projects/{member_project}/wiki/{path}",
                headers=headers,
                timeout=5,
            )
            assert resp.status_code == 200
            assert resp.json() == [], path

        searched = wiki_search(
            ctx["url"], session_id=sessions["read_member"], q="matrix"
        )
        assert searched.status_code == 200
        assert searched.json() == []

        tags = httpx.get(
            f"{ctx['url']}/api/v1/projects/{member_project}/wiki/tags",
            headers=headers,
            timeout=5,
        )
        assert tags.status_code == 200
        assert tags.json() == []

        assert (
            admit_member(
                ctx["url"], tag_id, member_project, "read", acknowledge_share=True
            ).status_code
            == 201
        )
        assert (
            wiki_get(
                ctx["url"],
                owner_project,
                "filtered",
                session_id=sessions["read_member"],
            ).status_code
            == 200
        )

        assert (
            set_member_access(
                ctx["url"],
                tag_id,
                member_project,
                "read_write",
                acknowledge_share=True,
            ).status_code
            == 200
        )
        assert (
            wiki_patch(
                ctx["url"],
                owner_project,
                "filtered",
                2,
                edits=[{"old_string": "matrix body", "new_string": "member body"}],
                session_id=sessions["read_member"],
            ).status_code
            == 200
        )

        assert revoke_member(ctx["url"], tag_id, owner_project).status_code == 204

        for suffix in ["", "/revisions", "/revisions/1"]:
            resp = httpx.get(
                f"{ctx['url']}/api/v1/projects/{owner_project}"
                f"/wiki/pages/filtered{suffix}",
                headers=headers,
                timeout=5,
            )
            assert resp.status_code == 404, f"owner-side {suffix!r} still readable"

        for path in ["pages", "changes"]:
            resp = httpx.get(
                f"{ctx['url']}/api/v1/projects/{member_project}/wiki/{path}",
                headers=headers,
                timeout=5,
            )
            assert resp.status_code == 200
            assert resp.json() == [], path

        searched = wiki_search(
            ctx["url"], session_id=sessions["read_member"], q="matrix"
        )
        assert searched.status_code == 200
        assert searched.json() == []

        tags = httpx.get(
            f"{ctx['url']}/api/v1/projects/{member_project}/wiki/tags",
            headers=headers,
            timeout=5,
        )
        assert tags.status_code == 200
        assert [t["name"] for t in tags.json()] == [SHARE_TAG]
        assert tags.json()[0]["page_count"] == 0

        write = wiki_patch(
            ctx["url"],
            owner_project,
            "filtered",
            3,
            edits=[{"old_string": "member body", "new_string": "later body"}],
            session_id=sessions["read_member"],
        )
        assert write.status_code == 404
        assert wiki_get(ctx["url"], owner_project, "filtered").json()["body"] == (
            "member body text"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_only_active_projects_participate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "orphaned", tagged=True)
        materialize_page(
            ctx["url"], projects["read_member"]["id"], "reader-home", tagged=False
        )
        materialize_page(
            ctx["url"], projects["write_member"]["id"], "writer-home", tagged=False
        )

        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}",
                timeout=5,
            ).status_code
            == 204
        )
        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "orphaned",
                session_id=sessions["read_member"],
            ).status_code
            == 404
        )

        searched = wiki_search(
            ctx["url"], session_id=sessions["read_member"], q="matrix"
        )
        assert searched.status_code == 200
        assert searched.json() == []

        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}", timeout=5
            ).status_code
            == 204
        )
        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['write_member']['id']}/wiki/pages",
            headers=bearer(sessions["write_member"]),
            timeout=5,
        )
        assert listing.status_code == 200
        assert [p["slug"] for p in listing.json()] == ["writer-home"]

        operator = wiki_search(ctx["url"], q="matrix")
        assert operator.status_code == 200
        assert [r["slug"] for r in operator.json()] == ["writer-home"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tag_foreign_key_does_not_cascade_on_delete(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            if ctx["db_url"].startswith("sqlite:"):
                rows = conn.execute(
                    "PRAGMA foreign_key_list(wiki_tag_members)"
                ).fetchall()
                tag_fks = [r for r in rows if r[2] == "wiki_tags"]
                assert len(tag_fks) == 1
                assert tag_fks[0][6] == "NO ACTION"
            else:
                rows = conn.execute(
                    "SELECT rc.delete_rule "
                    "FROM information_schema.referential_constraints rc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON kcu.constraint_name = rc.constraint_name "
                    "WHERE kcu.table_name = 'wiki_tag_members' "
                    "  AND kcu.column_name = 'tag_id'"
                ).fetchall()
                assert len(rows) == 1
                assert rows[0][0] == "NO ACTION"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_scope_defaults_to_own_project_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="scope-agent")
        mine = create_project_via_api(ctx["url"], "scope-mine")
        theirs = create_project_via_api(ctx["url"], "scope-theirs")
        _, session_id = create_execution_via_api(
            ctx["url"], agent_id, "x", project_id=mine["id"]
        )

        assert (
            wiki_put(
                ctx["url"], mine["id"], "mine", "Mine", "unicorn content"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], theirs["id"], "theirs", "Theirs", "unicorn content"
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], session_id=session_id, q="unicorn")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["mine"]

        narrowed = wiki_search(
            ctx["url"], session_id=session_id, q="unicorn", project=theirs["id"]
        )
        assert narrowed.status_code == 200
        assert narrowed.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_membership_admin_is_not_an_agent_facing_route(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)

        for path in [
            f"/api/v1/projects/{projects['owner']['id']}/wiki/share-tags",
            f"/api/v1/projects/{projects['owner']['id']}/wiki/tags/{tag_id}/members",
        ]:
            resp = httpx.get(
                f"{ctx['url']}{path}", headers=bearer(sessions["owner"]), timeout=5
            )
            assert resp.status_code == 404, f"{path} should not exist"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_attribution_stays_per_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "attributed", tagged=True)

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "attributed",
            2,
            edits=[{"old_string": "matrix body", "new_string": "member body"}],
            summary="member edit",
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200
        assert resp.json()["updated_by"] == sessions["write_member"]

        changes = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}/wiki/changes",
            headers=bearer(sessions["owner"]),
            timeout=5,
        )
        assert changes.status_code == 200
        latest = changes.json()[0]
        assert latest["created_by"] == sessions["write_member"]
        assert latest["summary"] == "member edit"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admitting_a_member_exposes_existing_pages_only_after_confirmation(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)
        materialize_page(
            ctx["url"], projects["owner"]["id"], "already-here", tagged=True
        )

        outsider = projects["outsider"]["id"]
        resp = admit_member(ctx["url"], tag_id, outsider, "read")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert data["tag"] == SHARE_TAG
        assert data["exposes_to_joiner"] == [
            {
                "project": projects["owner"]["slug"],
                "page_count": 1,
                "pages": [{"slug": "already-here", "title": "Matrix Page"}],
            }
        ]
        assert data["exposes_from_joiner"] == {"page_count": 0, "pages": []}
        assert "acknowledge_share" in data["remedy"]

        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "already-here",
                session_id=sessions["outsider"],
            ).status_code
            == 404
        )

        assert (
            admit_member(
                ctx["url"], tag_id, outsider, "read", acknowledge_share=True
            ).status_code
            == 201
        )
        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "already-here",
                session_id=sessions["outsider"],
            ).status_code
            == 200
        )
