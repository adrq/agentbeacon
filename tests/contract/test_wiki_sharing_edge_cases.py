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
    admit_member,
    bearer,
    find_wiki_tag,
    make_share_tag,
    set_member_access,
    wiki_get,
    wiki_put,
    wiki_search,
    ERR_CROSS_PROJECT_COLLECTION,
    SHARE_TAG,
    build_world,
    materialize_page,
)


def _project_with_session(ctx, agent_id, name):
    project = create_project_via_api(ctx["url"], name)
    _, session_id = create_execution_via_api(
        ctx["url"], agent_id, "task", project_id=project["id"]
    )
    return project, session_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_scope_is_not_a_cross_product_of_co_members_and_my_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="crossprod-agent")
        caller, caller_session = _project_with_session(ctx, agent_id, "xprod-caller")
        project_a, _ = _project_with_session(ctx, agent_id, "xprod-a")
        project_b, _ = _project_with_session(ctx, agent_id, "xprod-b")

        make_share_tag(
            ctx["url"],
            "t1",
            {caller["id"]: "read", project_a["id"]: "read_write"},
        )
        make_share_tag(
            ctx["url"],
            "t2",
            {caller["id"]: "read", project_b["id"]: "read_write"},
        )

        assert (
            wiki_put(
                ctx["url"],
                project_a["id"],
                "leaky",
                "Leaky",
                "cerulean content",
                tags=["t2"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"],
                project_a["id"],
                "legit",
                "Legit",
                "cerulean content",
                tags=["t1"],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        searched = wiki_search(ctx["url"], session_id=caller_session, q="cerulean")
        assert searched.status_code == 200
        assert [r["slug"] for r in searched.json()] == ["legit"]

        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{caller['id']}/wiki/pages",
            headers=bearer(caller_session),
            timeout=5,
        )
        assert listing.status_code == 200
        assert [p["slug"] for p in listing.json()] == ["legit"]

        assert (
            wiki_get(
                ctx["url"], project_a["id"], "leaky", session_id=caller_session
            ).status_code
            == 404
        )
        assert (
            wiki_get(
                ctx["url"], project_a["id"], "legit", session_id=caller_session
            ).status_code
            == 200
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_rebuild_all_indexes_every_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for n in range(3):
            project = create_project_via_api(ctx["url"], f"rebuild-{n}")
            assert (
                wiki_put(
                    ctx["url"],
                    project["id"],
                    f"page-{n}",
                    f"Page {n}",
                    "quokka content",
                ).status_code
                == 201
            )

    with scheduler_context(db_url=test_database) as ctx:
        operator = wiki_search(ctx["url"], q="quokka")
        assert operator.status_code == 200
        assert sorted(r["slug"] for r in operator.json()) == [
            "page-0",
            "page-1",
            "page-2",
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_publication_acknowledgment_required_on_all_three_paths(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="ack-agent")
        owner, _ = _project_with_session(ctx, agent_id, "ack-owner")
        peer, peer_session = _project_with_session(ctx, agent_id, "ack-peer")

        contract = make_share_tag(
            ctx["url"], "contract", {owner["id"]: "read_write", peer["id"]: "read"}
        )
        assert contract is not None

        assert wiki_put(ctx["url"], owner["id"], "p1", "P1", "body").status_code == 201
        resp = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{owner['id']}/wiki/pages/p1",
            json={"revision_number": 1, "add_tags": ["contract"]},
            timeout=5,
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "share_tag_requires_confirmation"

        resp = wiki_put(ctx["url"], owner["id"], "p2", "P2", "body", tags=["contract"])
        assert resp.status_code == 409
        assert resp.json()["error"] == "share_tag_requires_confirmation"

        for n in range(3):
            assert (
                wiki_put(
                    ctx["url"],
                    owner["id"],
                    f"bulk-{n}",
                    f"Bulk {n}",
                    "body",
                    tags=["bulk-tag"],
                ).status_code
                == 201
            )
        bulk = make_share_tag(ctx["url"], "bulk-tag", {owner["id"]: "read_write"})

        resp = admit_member(ctx["url"], bulk, peer["id"], "read")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert data["exposes_to_joiner"] == [
            {
                "project": owner["slug"],
                "page_count": 3,
                "pages": [
                    {"slug": "bulk-0", "title": "Bulk 0"},
                    {"slug": "bulk-1", "title": "Bulk 1"},
                    {"slug": "bulk-2", "title": "Bulk 2"},
                ],
            }
        ]
        assert data["exposes_from_joiner"] == {"page_count": 0, "pages": []}

        assert (
            wiki_get(
                ctx["url"], owner["id"], "bulk-0", session_id=peer_session
            ).status_code
            == 404
        )
        assert (
            admit_member(
                ctx["url"], bulk, peer["id"], "read", acknowledge_share=True
            ).status_code
            == 201
        )
        assert (
            wiki_get(
                ctx["url"], owner["id"], "bulk-0", session_id=peer_session
            ).status_code
            == 200
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_confirmation_fires_on_read_to_read_write_upgrade(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "already", tagged=True)
        materialize_page(ctx["url"], projects["owner"]["id"], "also", tagged=True)

        member = projects["read_member"]["id"]
        resp = set_member_access(ctx["url"], tag_id, member, "read_write")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert data["grants_write"] == [
            {
                "project": projects["owner"]["slug"],
                "page_count": 2,
                "pages": [
                    {"slug": "already", "title": "Matrix Page"},
                    {"slug": "also", "title": "Matrix Page"},
                ],
            }
        ]
        assert "exposes_to_joiner" not in data
        assert "exposes_from_joiner" not in data
        assert "acknowledge_share" in data["remedy"]

        entry = find_wiki_tag(ctx["url"], SHARE_TAG)
        levels = {m["project_id"]: m["access_level"] for m in entry["members"]}
        assert levels[member] == "read"
        assert entry["tag_id"] == tag_id

        assert (
            set_member_access(
                ctx["url"], tag_id, member, "read_write", acknowledge_share=True
            ).status_code
            == 200
        )

        assert set_member_access(ctx["url"], tag_id, member, "read").status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_caller_cannot_obtain_another_projects_merged_context(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "owner-page", tagged=True)
        materialize_page(
            ctx["url"], projects["write_member"]["id"], "third-party", tagged=True
        )

        assert (
            wiki_get(
                ctx["url"],
                projects["owner"]["id"],
                "owner-page",
                session_id=sessions["read_member"],
            ).status_code
            == 200
        )

        headers = bearer(sessions["read_member"])
        owner_project = projects["owner"]["id"]
        collections = [
            (f"/api/v1/projects/{owner_project}/wiki/pages", {}),
            (f"/api/v1/projects/{owner_project}/wiki/changes", {}),
            (f"/api/v1/projects/{owner_project}/wiki/tags", {}),
            (f"/api/v1/projects/{owner_project}/wiki/export", {}),
            (
                f"/api/v1/projects/{owner_project}/wiki/subscriptions",
                {"subscriber": "reader-agent"},
            ),
        ]
        for path, params in collections:
            resp = httpx.get(
                f"{ctx['url']}{path}", params=params, headers=headers, timeout=5
            )
            assert resp.status_code == 403, (
                f"{path} leaked another project's collection"
            )
            assert resp.json()["error"] == ERR_CROSS_PROJECT_COLLECTION

        narrowed = wiki_search(
            ctx["url"],
            session_id=sessions["read_member"],
            q="matrix",
            project=owner_project,
        )
        assert narrowed.status_code == 200
        assert [r["slug"] for r in narrowed.json()] == ["owner-page"]
