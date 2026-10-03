# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import re
import tempfile
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)
from tests.wiki_helpers import (
    find_wiki_tag,
    make_share_tag,
    wiki_get,
    wiki_put,
)


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def create_project(url, name, slug=None):
    payload = {"name": name, "path": tempfile.gettempdir()}
    if slug is not None:
        payload["slug"] = slug
    return httpx.post(f"{url}/api/v1/projects", json=payload, timeout=5)


def rename_project(url, project_id, slug):
    return httpx.patch(
        f"{url}/api/v1/projects/{project_id}", json={"slug": slug}, timeout=5
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_derives_from_an_ascii_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "My Cool Project")
        assert resp.status_code == 201
        assert resp.json()["slug"] == "my-cool-project"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_derivation_collapses_punctuation_runs(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "  Merge -- Manager!!  ")
        assert resp.status_code == 201
        assert resp.json()["slug"] == "merge-manager"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_derivation_from_a_name_with_no_slug_characters(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "!!! ???")
        assert resp.status_code == 201
        assert resp.json()["slug"] == resp.json()["id"]

        second = create_project(ctx["url"], "***")
        assert second.status_code == 201
        assert second.json()["slug"] == second.json()["id"]
        assert second.json()["slug"] != resp.json()["slug"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_derivation_from_a_wholly_non_ascii_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "日本語")
        assert resp.status_code == 201
        assert resp.json()["slug"] == resp.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_derivation_from_a_partly_non_ascii_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "Übergrößen Projekt")
        assert resp.status_code == 201
        assert resp.json()["slug"] == resp.json()["id"]

        resp = create_project(ctx["url"], "Café Zürich")
        assert resp.status_code == 201
        assert resp.json()["slug"] == resp.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ascii_names_are_unaffected_by_the_fallback(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for name, expected in [
            ("Merge Manager 2", "merge-manager-2"),
            ("api_contract (v3)", "api-contract-v3"),
            ("R&D / Notes", "r-d-notes"),
        ]:
            resp = create_project(ctx["url"], name)
            assert resp.status_code == 201, name
            assert resp.json()["slug"] == expected, name


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_derivation_from_an_overlong_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "a" * 250)
        assert resp.status_code == 201
        assert resp.json()["slug"] == "a" * 200

        resp = create_project(ctx["url"], "abc " * 60)
        assert resp.status_code == 201
        assert resp.json()["slug"] == "-".join(["abc"] * 50)[:199]
        assert resp.json()["slug"] == "abc-" * 49 + "abc"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_derived_slug_collision_is_rejected_not_suffixed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        first = create_project(ctx["url"], "Sandbox")
        assert first.status_code == 201
        assert first.json()["slug"] == "sandbox"

        second = create_project(ctx["url"], "Sandbox")
        assert second.status_code == 409
        assert second.json()["error"] == "slug_exists"

        listed = httpx.get(f"{ctx['url']}/api/v1/projects", timeout=5).json()
        assert [p["slug"] for p in listed] == ["sandbox"]

        explicit = create_project(ctx["url"], "Sandbox", slug="sandbox-two")
        assert explicit.status_code == 201
        assert explicit.json()["name"] == "Sandbox"
        assert explicit.json()["slug"] == "sandbox-two"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_a_name_that_is_another_uuid_does_not_derive_to_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        victim = create_project(ctx["url"], "Victim", slug="victim").json()

        resp = create_project(ctx["url"], victim["id"])
        assert resp.status_code == 201
        assert resp.json()["slug"] == resp.json()["id"]
        assert resp.json()["slug"] != victim["id"]

        assert (
            httpx.get(f"{ctx['url']}/api/v1/projects/{victim['id']}", timeout=5).json()[
                "id"
            ]
            == victim["id"]
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicit_slug_overrides_derivation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "Merge Manager", slug="mm")
        assert resp.status_code == 201
        assert resp.json()["slug"] == "mm"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicit_slug_collision_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        assert create_project(ctx["url"], "First", slug="taken").status_code == 201

        resp = create_project(ctx["url"], "Second", slug="taken")
        assert resp.status_code == 409
        assert resp.json()["error"] == "slug_exists"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicitly_blank_slug_is_rejected_at_create(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for blank in ["", "   ", "\t"]:
            resp = create_project(ctx["url"], "Blank Slug", slug=blank)
            assert resp.status_code == 400, f"slug {blank!r}: {resp.text}"
            assert resp.json()["error"] == "slug must be 1-200 characters"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicitly_blank_slug_is_rejected_on_update(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project(ctx["url"], "Rename Me", slug="keep-me")
        assert created.status_code == 201
        project_id = created.json()["id"]

        for blank in ["", "   ", "\t"]:
            resp = rename_project(ctx["url"], project_id, blank)
            assert resp.status_code == 400, f"slug {blank!r}: {resp.text}"
            assert resp.json()["error"] == "slug must be 1-200 characters"

        after = httpx.get(f"{ctx['url']}/api/v1/projects/{project_id}", timeout=5)
        assert after.status_code == 200
        assert after.json()["slug"] == "keep-me"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_an_absent_slug_still_derives_at_create(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        omitted = create_project(ctx["url"], "Derive From Name")
        assert omitted.status_code == 201
        assert omitted.json()["slug"] == "derive-from-name"

        explicit_null = httpx.post(
            f"{ctx['url']}/api/v1/projects",
            json={
                "name": "Derive From Null",
                "path": tempfile.gettempdir(),
                "slug": None,
            },
            timeout=5,
        )
        assert explicit_null.status_code == 201
        assert explicit_null.json()["slug"] == "derive-from-null"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_an_absent_slug_still_leaves_the_slug_alone_on_update(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_project(ctx["url"], "Untouched", slug="original-slug")
        assert created.status_code == 201
        project_id = created.json()["id"]

        renamed = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project_id}",
            json={"name": "Renamed But Not Reslugged"},
            timeout=5,
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Renamed But Not Reslugged"
        assert renamed.json()["slug"] == "original-slug"

        nulled = httpx.patch(
            f"{ctx['url']}/api/v1/projects/{project_id}",
            json={"slug": None},
            timeout=5,
        )
        assert nulled.status_code == 200
        assert nulled.json()["slug"] == "original-slug"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicit_uuid_shaped_slug_is_rejected_at_create(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = create_project(ctx["url"], "Shadow", slug=str(uuid.uuid4()))
        assert resp.status_code == 400
        assert "slug" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_a_project_may_take_its_own_id_as_its_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(ctx["url"], "Self Named", slug="self-named").json()

        resp = rename_project(ctx["url"], project["id"], project["id"])
        assert resp.status_code == 200
        assert resp.json()["slug"] == project["id"]

        fetched = httpx.get(f"{ctx['url']}/api/v1/projects/{project['id']}", timeout=5)
        assert fetched.status_code == 200
        assert fetched.json()["id"] == project["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_a_project_may_not_take_another_projects_id_as_its_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        victim = create_project(ctx["url"], "Victim", slug="victim").json()
        attacker = create_project(ctx["url"], "Attacker", slug="attacker").json()

        resp = rename_project(ctx["url"], attacker["id"], victim["id"])
        assert resp.status_code == 400
        assert "slug" in resp.json()["error"]

        fetched = httpx.get(f"{ctx['url']}/api/v1/projects/{victim['id']}", timeout=5)
        assert fetched.json()["id"] == victim["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_explicit_slug_must_match_the_wiki_slug_regex(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for bad in ["My Project", "trailing-", "-leading", "double--hyphen", "UPPER"]:
            resp = create_project(ctx["url"], "Bad Slug", slug=bad)
            assert resp.status_code == 400, f"slug {bad!r}"
            assert "slug" in resp.json()["error"]

        resp = create_project(ctx["url"], "Too Long", slug="a" * 201)
        assert resp.status_code == 400
        assert "slug" in resp.json()["error"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_is_reusable_after_soft_delete(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        first = create_project(ctx["url"], "Reusable", slug="reuse-me")
        assert first.status_code == 201
        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{first.json()['id']}", timeout=5
            ).status_code
            == 204
        )

        second = create_project(ctx["url"], "Reclaimer", slug="reuse-me")
        assert second.status_code == 201
        assert second.json()["id"] != first.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reclaimed_slug_resolves_to_the_new_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        first = create_project(ctx["url"], "Original", slug="claimed")
        assert (
            wiki_put(
                ctx["url"], first.json()["id"], "doc", "Old", "old body"
            ).status_code
            == 201
        )
        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{first.json()['id']}", timeout=5
            ).status_code
            == 204
        )

        second = create_project(ctx["url"], "Successor", slug="claimed")
        assert second.status_code == 201
        assert (
            wiki_put(
                ctx["url"], second.json()["id"], "doc", "New", "new body"
            ).status_code
            == 201
        )

        resp = wiki_get(ctx["url"], "claimed", "doc")
        assert resp.status_code == 200
        assert resp.json()["body"] == "new body"
        assert resp.json()["project_id"] == second.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_endpoints_accept_slug_or_uuid(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(ctx["url"], "Addressable", slug="addressable").json()

        by_uuid = httpx.get(f"{ctx['url']}/api/v1/projects/{project['id']}", timeout=5)
        by_slug = httpx.get(f"{ctx['url']}/api/v1/projects/addressable", timeout=5)
        assert by_uuid.status_code == 200
        assert by_slug.status_code == 200
        assert by_uuid.json()["id"] == by_slug.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_routes_accept_slug_or_uuid(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(
            ctx["url"], "Wiki Addressable", slug="wiki-addr"
        ).json()
        assert (
            wiki_put(ctx["url"], "wiki-addr", "doc", "Doc", "body").status_code == 201
        )

        by_uuid = wiki_get(ctx["url"], project["id"], "doc")
        by_slug = wiki_get(ctx["url"], "wiki-addr", "doc")
        assert by_uuid.status_code == 200
        assert by_slug.status_code == 200
        assert by_uuid.json()["id"] == by_slug.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_mutations_and_subroutes_accept_a_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(ctx["url"], "Sub Routes", slug="subroutes").json()

        for path in ["/agents", "/mcp-servers"]:
            by_uuid = httpx.get(
                f"{ctx['url']}/api/v1/projects/{project['id']}{path}", timeout=5
            )
            by_slug = httpx.get(
                f"{ctx['url']}/api/v1/projects/subroutes{path}", timeout=5
            )
            assert by_uuid.status_code == 200, path
            assert by_slug.status_code == 200, path
            assert by_slug.json() == by_uuid.json(), path

        renamed = httpx.patch(
            f"{ctx['url']}/api/v1/projects/subroutes",
            json={"name": "Renamed By Slug"},
            timeout=5,
        )
        assert renamed.status_code == 200
        assert renamed.json()["id"] == project["id"]
        assert renamed.json()["name"] == "Renamed By Slug"

        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/subroutes", timeout=5
            ).status_code
            == 204
        )
        assert (
            httpx.get(
                f"{ctx['url']}/api/v1/projects/{project['id']}", timeout=5
            ).status_code
            == 404
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unknown_slug_returns_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        assert (
            httpx.get(f"{ctx['url']}/api/v1/projects/nope", timeout=5).status_code
            == 404
        )
        assert wiki_get(ctx["url"], "nope", "doc").status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_of_a_soft_deleted_project_no_longer_resolves(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(ctx["url"], "Departing", slug="departing").json()
        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{project['id']}", timeout=5
            ).status_code
            == 204
        )

        assert (
            httpx.get(f"{ctx['url']}/api/v1/projects/departing", timeout=5).status_code
            == 404
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_id_stays_a_uuid_in_payloads(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(ctx["url"], "Explicit Fields", slug="explicit").json()
        assert UUID_RE.match(project["id"])
        assert project["slug"] == "explicit"

        page = wiki_put(ctx["url"], "explicit", "doc", "Doc", "body")
        assert page.status_code == 201
        assert page.json()["project_id"] == project["id"]
        assert page.json()["project_slug"] == "explicit"

        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/explicit/wiki/pages", timeout=5
        )
        assert listing.status_code == 200
        assert listing.json()[0]["project_id"] == project["id"]
        assert listing.json()[0]["project_slug"] == "explicit"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_rename_changes_addressing_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project(ctx["url"], "Renamable", slug="before").json()
        assert wiki_put(ctx["url"], "before", "doc", "Doc", "body").status_code == 201

        resp = rename_project(ctx["url"], project["id"], "after")
        assert resp.status_code == 200
        assert resp.json()["slug"] == "after"

        assert wiki_get(ctx["url"], "after", "doc").status_code == 200
        assert wiki_get(ctx["url"], "before", "doc").status_code == 404
        assert wiki_get(ctx["url"], project["id"], "doc").status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_rename_leaves_membership_intact(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha = create_project(ctx["url"], "Alpha", slug="alpha").json()
        beta = create_project(ctx["url"], "Beta", slug="beta").json()
        tag_id = make_share_tag(
            ctx["url"], "stable", {"alpha": "read_write", "beta": "read"}
        )

        assert (
            rename_project(ctx["url"], alpha["id"], "alpha-renamed").status_code == 200
        )

        entry = find_wiki_tag(ctx["url"], "stable")
        assert entry["tag_id"] == tag_id
        members = {m["project_id"]: m for m in entry["members"]}
        assert members[alpha["id"]]["access_level"] == "read_write"
        assert members[alpha["id"]]["project_slug"] == "alpha-renamed"
        assert members[beta["id"]]["access_level"] == "read"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_rename_to_a_taken_slug_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_project(ctx["url"], "One", slug="one")
        two = create_project(ctx["url"], "Two", slug="two").json()

        resp = rename_project(ctx["url"], two["id"], "one")
        assert resp.status_code == 409
        assert resp.json()["error"] == "slug_exists"

        assert (
            httpx.get(f"{ctx['url']}/api/v1/projects/two", timeout=5).status_code == 200
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_retired_slug_may_be_reclaimed_after_rename(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        first = create_project(ctx["url"], "Mover", slug="wanted").json()
        assert rename_project(ctx["url"], first["id"], "moved").status_code == 200

        second = create_project(ctx["url"], "Claimant", slug="wanted")
        assert second.status_code == 201
        assert (
            httpx.get(f"{ctx['url']}/api/v1/projects/wanted", timeout=5).json()["id"]
            == second.json()["id"]
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_every_project_has_a_unique_valid_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        expected = {}
        for name in ["Alpha", "Ünïcödé", "!!!", "Beta Gamma"]:
            resp = create_project(ctx["url"], name)
            assert resp.status_code == 201, name
            expected[name] = resp.json()["slug"]

        assert expected["Alpha"] == "alpha"
        assert expected["Beta Gamma"] == "beta-gamma"
        assert UUID_RE.match(expected["Ünïcödé"])
        assert UUID_RE.match(expected["!!!"])

        resp = httpx.get(f"{ctx['url']}/api/v1/projects", timeout=5)
        assert resp.status_code == 200
        slugs = [p["slug"] for p in resp.json()]
        assert sorted(slugs) == sorted(expected.values())
        assert all(SLUG_RE.match(s) for s in slugs), slugs
        assert len(set(slugs)) == 4


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_projects_created_by_the_shared_helper_expose_a_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "helper-project")
        assert project["slug"] == "helper-project"
