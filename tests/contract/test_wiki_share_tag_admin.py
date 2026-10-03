# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)
from tests.wiki_helpers import (
    admit_member,
    find_wiki_tag,
    list_wiki_tags,
    make_share_tag,
    membership_rows,
    revoke_member,
    seed_tag,
    set_member_access,
    tag_id_for,
    wiki_put,
)


def two_projects(ctx):
    return (
        create_project_via_api(ctx["url"], "admin-alpha"),
        create_project_via_api(ctx["url"], "admin-beta"),
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tags_come_into_existence_by_being_applied_to_a_page(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)
        assert list_wiki_tags(ctx["url"]).json() == []

        assert (
            wiki_put(
                ctx["url"], alpha["id"], "doc", "Doc", "body", tags=["agent-made"]
            ).status_code
            == 201
        )

        entry = find_wiki_tag(ctx["url"], "agent-made")
        assert entry is not None
        assert entry["members"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_to_a_nonexistent_tag_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)

        resp = admit_member(ctx["url"], str(uuid.uuid4()), alpha["id"], "read")
        assert resp.status_code == 404
        assert resp.json()["error"] == "tag_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_listing_returns_all_tags_not_only_share_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        assert (
            wiki_put(
                ctx["url"], alpha["id"], "doc", "Doc", "body", tags=["plain-tag"]
            ).status_code
            == 201
        )
        make_share_tag(ctx["url"], "zebra", {alpha["id"]: "read"})
        make_share_tag(ctx["url"], "aardvark", {beta["id"]: "read"})

        resp = list_wiki_tags(ctx["url"])
        assert resp.status_code == 200
        entries = {t["tag"]: t for t in resp.json()}
        assert sorted(entries) == ["aardvark", "plain-tag", "zebra"]
        assert entries["plain-tag"]["members"] == []
        assert [m["project_id"] for m in entries["zebra"]["members"]] == [alpha["id"]]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_listing_is_ordered_by_tag_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "doc",
                "Doc",
                "body",
                tags=["gamma", "alpha", "beta"],
            ).status_code
            == 201
        )

        resp = list_wiki_tags(ctx["url"])
        assert resp.status_code == 200
        assert [t["tag"] for t in resp.json()] == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_listing_carries_members_inline_with_project_slugs(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "inline", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        entry = find_wiki_tag(ctx["url"], "inline")
        assert entry["tag_id"] == tag_id
        members = {m["project_id"]: m for m in entry["members"]}
        assert members[alpha["id"]]["access_level"] == "read_write"
        assert members[alpha["id"]]["project_slug"] == alpha["slug"]
        assert members[beta["id"]]["access_level"] == "read"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_first_admission_designates_the_tag(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        seed_tag(ctx["url"], "designated")
        tag_id = tag_id_for(ctx["url"], "designated")

        resp = admit_member(ctx["url"], tag_id, alpha["id"], "read_write")
        assert resp.status_code == 201
        assert resp.json()["project_id"] == alpha["id"]
        assert resp.json()["access_level"] == "read_write"

        assert admit_member(ctx["url"], tag_id, beta["id"], "read").status_code == 201
        entry = find_wiki_tag(ctx["url"], "designated")
        assert sorted(m["project_id"] for m in entry["members"]) == sorted(
            [alpha["id"], beta["id"]]
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_designating_a_tag_publishes_what_already_carries_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "bootstrap",
                "Bootstrap",
                "body",
                tags=["fresh"],
            ).status_code
            == 201
        )
        tag_id = tag_id_for(ctx["url"], "fresh")

        assert (
            admit_member(ctx["url"], tag_id, alpha["id"], "read_write").status_code
            == 201
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert data["exposes_to_joiner"] == [
            {
                "project": alpha["slug"],
                "page_count": 1,
                "pages": [{"slug": "bootstrap", "title": "Bootstrap"}],
            }
        ]
        assert data["exposes_from_joiner"] == {"page_count": 0, "pages": []}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_confirmation_payload_carries_the_affected_pages(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        for slug, title in [("alpha-doc", "Alpha Doc"), ("beta-doc", "Beta Doc")]:
            assert (
                wiki_put(
                    ctx["url"], alpha["id"], slug, title, "body", tags=["inspectable"]
                ).status_code
                == 201
            )
        tag_id = tag_id_for(ctx["url"], "inspectable")
        assert (
            admit_member(ctx["url"], tag_id, alpha["id"], "read_write").status_code
            == 201
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 409
        data = resp.json()
        assert data["tag"] == "inspectable"
        assert data["exposes_to_joiner"] == [
            {
                "project": alpha["slug"],
                "page_count": 2,
                "pages": [
                    {"slug": "alpha-doc", "title": "Alpha Doc"},
                    {"slug": "beta-doc", "title": "Beta Doc"},
                ],
            }
        ]
        assert "acknowledge_share" in data["remedy"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_confirmation_page_list_is_capped_at_fifty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        for n in range(55):
            assert (
                wiki_put(
                    ctx["url"],
                    alpha["id"],
                    f"doc-{n:02d}",
                    f"Doc {n}",
                    "body",
                    tags=["sprawling"],
                ).status_code
                == 201
            )
        tag_id = tag_id_for(ctx["url"], "sprawling")
        assert (
            admit_member(ctx["url"], tag_id, alpha["id"], "read_write").status_code
            == 201
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 409
        exposed = resp.json()["exposes_to_joiner"]
        assert len(exposed) == 1
        assert exposed[0]["project"] == alpha["slug"]
        assert exposed[0]["page_count"] == 55
        assert len(exposed[0]["pages"]) == 50
        assert [p["slug"] for p in exposed[0]["pages"]] == [
            f"doc-{n:02d}" for n in range(50)
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_confirmation_names_both_directions_of_exposure(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        gamma = create_project_via_api(ctx["url"], "admin-gamma")

        for project, slug, title in [
            (alpha, "alpha-doc", "Alpha Doc"),
            (gamma, "gamma-doc", "Gamma Doc"),
            (beta, "joiner-one", "Joiner One"),
            (beta, "joiner-two", "Joiner Two"),
        ]:
            assert (
                wiki_put(
                    ctx["url"], project["id"], slug, title, "body", tags=["twoway"]
                ).status_code
                == 201
            )
        tag_id = tag_id_for(ctx["url"], "twoway")
        for incumbent in [alpha, gamma]:
            assert (
                admit_member(
                    ctx["url"],
                    tag_id,
                    incumbent["id"],
                    "read",
                    acknowledge_share=True,
                ).status_code
                == 201
            )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 409
        data = resp.json()
        assert data["exposes_to_joiner"] == [
            {
                "project": alpha["slug"],
                "page_count": 1,
                "pages": [{"slug": "alpha-doc", "title": "Alpha Doc"}],
            },
            {
                "project": gamma["slug"],
                "page_count": 1,
                "pages": [{"slug": "gamma-doc", "title": "Gamma Doc"}],
            },
        ]
        assert data["exposes_from_joiner"] == {
            "page_count": 2,
            "pages": [
                {"slug": "joiner-one", "title": "Joiner One"},
                {"slug": "joiner-two", "title": "Joiner Two"},
            ],
        }

        assert (
            admit_member(
                ctx["url"], tag_id, beta["id"], "read", acknowledge_share=True
            ).status_code
            == 201
        )
        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{alpha['id']}/wiki/pages", timeout=5
        )
        assert sorted(p["slug"] for p in listing.json()) == [
            "alpha-doc",
            "gamma-doc",
            "joiner-one",
            "joiner-two",
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_joiner_side_page_list_is_capped_at_fifty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "incumbent",
                "Incumbent",
                "body",
                tags=["outbound"],
            ).status_code
            == 201
        )
        for n in range(52):
            assert (
                wiki_put(
                    ctx["url"],
                    beta["id"],
                    f"joiner-{n:02d}",
                    f"Joiner {n}",
                    "body",
                    tags=["outbound"],
                ).status_code
                == 201
            )
        tag_id = tag_id_for(ctx["url"], "outbound")
        assert (
            admit_member(
                ctx["url"], tag_id, alpha["id"], "read", acknowledge_share=True
            ).status_code
            == 201
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 409
        outward = resp.json()["exposes_from_joiner"]
        assert outward["page_count"] == 52
        assert len(outward["pages"]) == 50
        assert [p["slug"] for p in outward["pages"]] == [
            f"joiner-{n:02d}" for n in range(50)
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_upgrade_confirmation_reports_write_capability_not_exposure(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        for slug, title in [("owned-a", "Owned A"), ("owned-b", "Owned B")]:
            assert (
                wiki_put(
                    ctx["url"], alpha["id"], slug, title, "body", tags=["upgradeable"]
                ).status_code
                == 201
            )
        gamma = create_project_via_api(ctx["url"], "admin-gamma")
        assert (
            wiki_put(
                ctx["url"],
                gamma["id"],
                "owned-c",
                "Owned C",
                "body",
                tags=["upgradeable"],
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], beta["id"], "mine", "Mine", "body", tags=["upgradeable"]
            ).status_code
            == 201
        )
        tag_id = make_share_tag(
            ctx["url"],
            "upgradeable",
            {
                alpha["id"]: "read_write",
                gamma["id"]: "read_write",
                beta["id"]: "read",
            },
        )

        resp = set_member_access(ctx["url"], tag_id, beta["id"], "read_write")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert data["tag"] == "upgradeable"
        assert data["grants_write"] == [
            {
                "project": alpha["slug"],
                "page_count": 2,
                "pages": [
                    {"slug": "owned-a", "title": "Owned A"},
                    {"slug": "owned-b", "title": "Owned B"},
                ],
            },
            {
                "project": gamma["slug"],
                "page_count": 1,
                "pages": [{"slug": "owned-c", "title": "Owned C"}],
            },
        ]
        assert "exposes_to_joiner" not in data
        assert "exposes_from_joiner" not in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_blast_radius_excludes_revoked_and_deleted_members(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)
        departed = create_project_via_api(ctx["url"], "admin-departed")
        deleted = create_project_via_api(ctx["url"], "admin-deleted")
        joiner = create_project_via_api(ctx["url"], "admin-joiner")

        for project, slug in [
            (alpha, "live-page"),
            (departed, "departed-page"),
            (deleted, "deleted-page"),
        ]:
            assert (
                wiki_put(
                    ctx["url"], project["id"], slug, "Doc", "body", tags=["radius"]
                ).status_code
                == 201
            )
        tag_id = make_share_tag(
            ctx["url"],
            "radius",
            {
                alpha["id"]: "read_write",
                departed["id"]: "read_write",
                deleted["id"]: "read_write",
            },
        )

        assert revoke_member(ctx["url"], tag_id, departed["id"]).status_code == 204
        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{deleted['id']}", timeout=5
            ).status_code
            == 204
        )

        resp = admit_member(ctx["url"], tag_id, joiner["id"], "read")
        assert resp.status_code == 409
        assert resp.json()["exposes_to_joiner"] == [
            {
                "project": alpha["slug"],
                "page_count": 1,
                "pages": [{"slug": "live-page", "title": "Doc"}],
            }
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_needs_no_confirmation_when_nothing_becomes_visible(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        seed_tag(ctx["url"], "inert")
        tag_id = tag_id_for(ctx["url"], "inert")

        assert (
            admit_member(ctx["url"], tag_id, alpha["id"], "read_write").status_code
            == 201
        )
        assert admit_member(ctx["url"], tag_id, beta["id"], "read").status_code == 201


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_accepts_a_project_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)
        seed_tag(ctx["url"], "by-slug")
        tag_id = tag_id_for(ctx["url"], "by-slug")

        resp = admit_member(ctx["url"], tag_id, alpha["slug"], "read")
        assert resp.status_code == 201
        assert resp.json()["project_id"] == alpha["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admitting_an_existing_member_at_the_same_level_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "repeat", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 200
        assert len(membership_rows(ctx["db_url"], "repeat", beta["id"])) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admitting_an_existing_member_at_a_different_level_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "conflicting", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read_write")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "member_exists"
        assert data["access_level"] == "read"
        assert "PATCH" in data["remedy"]

        assert membership_rows(ctx["db_url"], "conflicting", beta["id"])[0][1] == "read"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_rejects_an_unknown_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        seed_tag(ctx["url"], "guarded")
        tag_id = tag_id_for(ctx["url"], "guarded")

        resp = admit_member(ctx["url"], tag_id, str(uuid.uuid4()), "read")
        assert resp.status_code == 404
        assert resp.json()["error"] == "project_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_rejects_a_soft_deleted_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(ctx["url"], "guarded", {alpha["id"]: "read_write"})

        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{beta['id']}", timeout=5
            ).status_code
            == 204
        )

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 404
        assert resp.json()["error"] == "project_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_rejects_an_unknown_access_level(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(ctx["url"], "levels", {alpha["id"]: "read_write"})

        for bad in ["owner", "", "READ"]:
            resp = admit_member(ctx["url"], tag_id, beta["id"], bad)
            assert resp.status_code == 400, f"access_level {bad!r}"
            assert "access_level" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_access_upgrade_overwrites_in_place(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "upgradable", {alpha["id"]: "read_write", beta["id"]: "read"}
        )
        before = membership_rows(ctx["db_url"], "upgradable", beta["id"])

        resp = set_member_access(
            ctx["url"], tag_id, beta["id"], "read_write", acknowledge_share=True
        )
        assert resp.status_code == 200
        assert resp.json()["access_level"] == "read_write"

        after = membership_rows(ctx["db_url"], "upgradable", beta["id"])
        assert len(after) == 1
        assert after[0][0] == before[0][0]
        assert after[0][1] == "read_write"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_setting_the_same_access_level_is_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "unchanged", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        resp = set_member_access(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 200
        assert resp.json()["access_level"] == "read"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_access_change_on_a_revoked_member_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "revoked-level", {alpha["id"]: "read_write", beta["id"]: "read"}
        )
        assert revoke_member(ctx["url"], tag_id, beta["id"]).status_code == 204

        resp = set_member_access(
            ctx["url"], tag_id, beta["id"], "read_write", acknowledge_share=True
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "member_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_access_change_rejects_an_unknown_level(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "bad-level", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        resp = set_member_access(ctx["url"], tag_id, beta["id"], "write")
        assert resp.status_code == 400
        assert "access_level" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_access_change_on_a_nonexistent_tag_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)

        resp = set_member_access(
            ctx["url"], str(uuid.uuid4()), alpha["id"], "read_write"
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "tag_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revocation_is_soft(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "soft", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        assert revoke_member(ctx["url"], tag_id, beta["id"]).status_code == 204
        rows = membership_rows(ctx["db_url"], "soft", beta["id"])
        assert len(rows) == 1
        assert rows[0][2] is not None

        entry = find_wiki_tag(ctx["url"], "soft")
        assert [m["project_id"] for m in entry["members"]] == [alpha["id"]]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tag_stays_listed_with_no_members_once_the_last_is_revoked(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)
        tag_id = make_share_tag(ctx["url"], "fleeting", {alpha["id"]: "read"})

        assert revoke_member(ctx["url"], tag_id, alpha["id"]).status_code == 204

        entry = find_wiki_tag(ctx["url"], "fleeting")
        assert entry is not None
        assert entry["tag_id"] == tag_id
        assert entry["members"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revoking_a_non_member_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(ctx["url"], "single", {alpha["id"]: "read_write"})

        resp = revoke_member(ctx["url"], tag_id, beta["id"])
        assert resp.status_code == 404
        assert resp.json()["error"] == "member_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revoking_twice_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "twice", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        assert revoke_member(ctx["url"], tag_id, beta["id"]).status_code == 204
        second = revoke_member(ctx["url"], tag_id, beta["id"])
        assert second.status_code == 404
        assert second.json()["error"] == "member_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_re_admission_inserts_a_new_row(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "returning", {alpha["id"]: "read_write", beta["id"]: "read"}
        )
        assert revoke_member(ctx["url"], tag_id, beta["id"]).status_code == 204

        resp = admit_member(
            ctx["url"], tag_id, beta["id"], "read", acknowledge_share=True
        )
        assert resp.status_code == 201

        rows = membership_rows(ctx["db_url"], "returning", beta["id"])
        assert len(rows) == 2
        assert len([r for r in rows if r[2] is None]) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_re_admission_requires_confirmation_when_pages_exist(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        assert (
            wiki_put(
                ctx["url"], alpha["id"], "doc", "Doc", "body", tags=["boomerang"]
            ).status_code
            == 201
        )
        tag_id = make_share_tag(
            ctx["url"], "boomerang", {alpha["id"]: "read_write", beta["id"]: "read"}
        )
        assert revoke_member(ctx["url"], tag_id, beta["id"]).status_code == 204

        resp = admit_member(ctx["url"], tag_id, beta["id"], "read")
        assert resp.status_code == 409
        assert resp.json()["error"] == "membership_requires_confirmation"
        assert resp.json()["exposes_to_joiner"] == [
            {
                "project": alpha["slug"],
                "page_count": 1,
                "pages": [{"slug": "doc", "title": "Doc"}],
            }
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revoke_and_re_admit_leaves_no_active_duplicate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        tag_id = make_share_tag(
            ctx["url"], "cycled", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        for _ in range(3):
            assert revoke_member(ctx["url"], tag_id, beta["id"]).status_code == 204
            assert (
                admit_member(
                    ctx["url"], tag_id, beta["id"], "read", acknowledge_share=True
                ).status_code
                == 201
            )

        rows = membership_rows(ctx["db_url"], "cycled", beta["id"])
        assert len(rows) == 4
        assert len([r for r in rows if r[2] is None]) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revocation_on_a_nonexistent_tag_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _ = two_projects(ctx)

        resp = revoke_member(ctx["url"], str(uuid.uuid4()), alpha["id"])
        assert resp.status_code == 404
        assert resp.json()["error"] == "tag_not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_soft_deleted_project_drops_out_of_the_member_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, beta = two_projects(ctx)
        make_share_tag(
            ctx["url"], "outliving", {alpha["id"]: "read_write", beta["id"]: "read"}
        )

        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{beta['id']}", timeout=5
            ).status_code
            == 204
        )

        entry = find_wiki_tag(ctx["url"], "outliving")
        assert [m["project_id"] for m in entry["members"]] == [alpha["id"]]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_outbound_radius_counts_a_page_one_incumbent_cannot_already_see(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        joiner = create_project_via_api(ctx["url"], "radius-joiner")
        sees_it = create_project_via_api(ctx["url"], "radius-sees")
        blind = create_project_via_api(ctx["url"], "radius-blind")

        assert (
            wiki_put(
                ctx["url"],
                joiner["id"],
                "joiner-page",
                "Joiner Page",
                "body",
                tags=["radius-main", "radius-side"],
            ).status_code
            == 201
        )

        make_share_tag(
            ctx["url"],
            "radius-side",
            {joiner["id"]: "read", sees_it["id"]: "read"},
        )
        make_share_tag(
            ctx["url"],
            "radius-main",
            {sees_it["id"]: "read", blind["id"]: "read"},
        )
        tag_id = tag_id_for(ctx["url"], "radius-main")

        resp = admit_member(ctx["url"], tag_id, joiner["id"], "read")
        assert resp.status_code == 409, resp.text
        outward = resp.json()["exposes_from_joiner"]
        assert outward["page_count"] == 1, (
            "the page is new to the incumbent that cannot already see it"
        )
        assert [p["slug"] for p in outward["pages"]] == ["joiner-page"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_at_read_write_discloses_write_it_grants_over_readable_pages(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        owner = create_project_via_api(ctx["url"], "rw-owner")
        joiner = create_project_via_api(ctx["url"], "rw-joiner")

        for slug, title in [("shared-one", "Shared One"), ("shared-two", "Shared Two")]:
            assert (
                wiki_put(
                    ctx["url"],
                    owner["id"],
                    slug,
                    title,
                    "body",
                    tags=["reading-room", "workshop"],
                    acknowledge_share=True,
                ).status_code
                == 201
            )

        make_share_tag(
            ctx["url"],
            "reading-room",
            {owner["id"]: "read_write", joiner["id"]: "read"},
        )

        workshop = make_share_tag(ctx["url"], "workshop", {owner["id"]: "read_write"})

        resp = admit_member(ctx["url"], workshop, joiner["id"], "read_write")
        assert resp.status_code == 409, resp.text
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert data["tag"] == "workshop"

        assert data["exposes_to_joiner"] == []
        assert data["exposes_from_joiner"] == {"page_count": 0, "pages": []}

        assert data["grants_write"] == [
            {
                "project": owner["slug"],
                "page_count": 2,
                "pages": [
                    {"slug": "shared-one", "title": "Shared One"},
                    {"slug": "shared-two", "title": "Shared Two"},
                ],
            }
        ]

        entry = find_wiki_tag(ctx["url"], "workshop")
        assert [m["project_id"] for m in entry["members"]] == [owner["id"]]

        assert (
            admit_member(
                ctx["url"], workshop, joiner["id"], "read_write", acknowledge_share=True
            ).status_code
            == 201
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_grants_write_is_the_same_whether_admitted_at_read_write_or_upgraded(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:

        def world(suffix):
            owner = create_project_via_api(ctx["url"], f"path-owner-{suffix}")
            joiner = create_project_via_api(ctx["url"], f"path-joiner-{suffix}")
            for slug, title in [("alpha-doc", "Alpha Doc"), ("beta-doc", "Beta Doc")]:
                assert (
                    wiki_put(
                        ctx["url"],
                        owner["id"],
                        slug,
                        title,
                        "body",
                        tags=[f"tag-{suffix}"],
                        acknowledge_share=True,
                    ).status_code
                    == 201
                )
            tag = make_share_tag(
                ctx["url"], f"tag-{suffix}", {owner["id"]: "read_write"}
            )
            return owner, joiner, tag

        _, joiner_a, tag_a = world("direct")
        direct = admit_member(ctx["url"], tag_a, joiner_a["id"], "read_write")
        assert direct.status_code == 409, direct.text

        _, joiner_b, tag_b = world("stepwise")
        assert (
            admit_member(
                ctx["url"], tag_b, joiner_b["id"], "read", acknowledge_share=True
            ).status_code
            == 201
        )
        stepwise = set_member_access(ctx["url"], tag_b, joiner_b["id"], "read_write")
        assert stepwise.status_code == 409, stepwise.text

        def pages_only(entries):
            return [(e["page_count"], e["pages"]) for e in entries]

        assert pages_only(direct.json()["grants_write"]) == pages_only(
            stepwise.json()["grants_write"]
        )
        assert pages_only(direct.json()["grants_write"]) == [
            (
                2,
                [
                    {"slug": "alpha-doc", "title": "Alpha Doc"},
                    {"slug": "beta-doc", "title": "Beta Doc"},
                ],
            )
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_admission_at_read_does_not_report_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        owner = create_project_via_api(ctx["url"], "readonly-owner")
        joiner = create_project_via_api(ctx["url"], "readonly-joiner")
        assert (
            wiki_put(
                ctx["url"],
                owner["id"],
                "a-page",
                "A Page",
                "body",
                tags=["reading-only"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        tag = make_share_tag(ctx["url"], "reading-only", {owner["id"]: "read_write"})

        resp = admit_member(ctx["url"], tag, joiner["id"], "read")
        assert resp.status_code == 409, resp.text
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        assert "grants_write" not in data
        assert data["exposes_to_joiner"] == [
            {
                "project": owner["slug"],
                "page_count": 1,
                "pages": [{"slug": "a-page", "title": "A Page"}],
            }
        ]
