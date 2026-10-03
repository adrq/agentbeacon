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
    make_share_tag,
    untag_page_in_db,
    wiki_get,
    wiki_put,
    wiki_search,
)


def two_project_world(ctx, tag=None):
    agent_id = seed_test_agent(ctx["db_url"], name="search-agent")
    alpha = create_project_via_api(ctx["url"], "search-alpha")
    beta = create_project_via_api(ctx["url"], "search-beta")
    _, alpha_session = create_execution_via_api(
        ctx["url"], agent_id, "s", project_id=alpha["id"]
    )
    _, beta_session = create_execution_via_api(
        ctx["url"], agent_id, "s", project_id=beta["id"]
    )
    if tag is not None:
        make_share_tag(ctx["url"], tag, {alpha["id"]: "read_write", beta["id"]: "read"})
    return alpha, alpha_session, beta, beta_session


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_requires_a_query(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_project_via_api(ctx["url"], "search-q")

        for params in [{}, {"q": ""}, {"q": "   "}]:
            resp = wiki_search(ctx["url"], **params)
            assert resp.status_code == 400, f"with {params}"
            assert resp.json()["error"] == "q is required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_no_matches_returns_empty_array(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-empty")
        assert (
            wiki_put(ctx["url"], project["id"], "p", "Page", "content").status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_query_with_no_usable_terms_returns_nothing_not_everything(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-no-terms")
        for n in range(3):
            assert (
                wiki_put(
                    ctx["url"], project["id"], f"p{n}", f"Page {n}", "content"
                ).status_code
                == 201
            )

        for query in ["+++", "!!!", "---"]:
            resp = wiki_search(ctx["url"], q=query)
            assert resp.status_code == 200, query
            assert resp.json() == [], query


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leading_minus_is_a_literal_term_not_an_exclusion(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-minus")
        assert (
            wiki_put(
                ctx["url"], project["id"], "has-foo", "Has Foo", "foo bar"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], project["id"], "no-foo", "Absent", "baz qux"
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="-foo")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["has-foo"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_colon_is_a_literal_separator_not_a_field_query(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-colon")
        assert (
            wiki_put(ctx["url"], project["id"], "a", "Aaa", "alpha content").status_code
            == 201
        )
        assert (
            wiki_put(ctx["url"], project["id"], "b", "Bbb", "beta content").status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="title:alpha")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["a"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_quotes_are_not_a_phrase_query(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-quotes")
        assert (
            wiki_put(ctx["url"], project["id"], "red", "Red", "red fish").status_code
            == 201
        )
        assert (
            wiki_put(ctx["url"], project["id"], "blue", "Blue", "blue fish").status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q='"red blue"')
        assert resp.status_code == 200
        assert sorted(r["slug"] for r in resp.json()) == ["blue", "red"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_boolean_keywords_are_literal_terms(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-keywords")
        assert (
            wiki_put(
                ctx["url"], project["id"], "hit", "Hit", "gamma content"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], project["id"], "miss", "Miss", "delta content"
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="gamma AND epsilon")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["hit"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_code_symbols_match_as_terms(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-symbols")
        assert (
            wiki_put(
                ctx["url"], project["id"], "cpp", "C++ Programming", "templates"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], project["id"], "other", "Rust Guide", "ownership"
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="C++")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["cpp"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_limit_and_offset_page_through_results(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-paging")
        for n in range(5):
            assert (
                wiki_put(
                    ctx["url"], project["id"], f"p{n}", f"Page {n}", "narwhal content"
                ).status_code
                == 201
            )

        first = wiki_search(ctx["url"], q="narwhal", limit=2, offset=0)
        second = wiki_search(ctx["url"], q="narwhal", limit=2, offset=2)
        rest = wiki_search(ctx["url"], q="narwhal", limit=2, offset=4)
        assert [r.status_code for r in [first, second, rest]] == [200, 200, 200]
        assert [len(r.json()) for r in [first, second, rest]] == [2, 2, 1]

        paged = [r["slug"] for r in first.json() + second.json() + rest.json()]
        unpaged = [r["slug"] for r in wiki_search(ctx["url"], q="narwhal").json()]
        assert paged == unpaged


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_default_limit_is_fifty(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-default-limit")
        for n in range(55):
            assert (
                wiki_put(
                    ctx["url"],
                    project["id"],
                    f"p{n:02d}",
                    f"Page {n}",
                    "axolotl content",
                ).status_code
                == 201
            )

        resp = wiki_search(ctx["url"], q="axolotl")
        assert resp.status_code == 200
        assert len(resp.json()) == 50


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_rejects_out_of_range_limit_and_offset(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_project_via_api(ctx["url"], "search-bounds")

        for limit in [0, -1, 101]:
            resp = wiki_search(ctx["url"], q="anything", limit=limit)
            assert resp.status_code == 400, f"limit={limit}"
            assert resp.json()["error"] == "limit must be between 1 and 100"

        resp = wiki_search(ctx["url"], q="anything", offset=-1)
        assert resp.status_code == 400
        assert resp.json()["error"] == "offset must be >= 0"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_rejects_non_integer_limit_and_offset(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_project_via_api(ctx["url"], "search-badparams")

        resp = wiki_search(ctx["url"], q="anything", limit="lots")
        assert resp.status_code == 400
        assert resp.json()["error"] == "limit must be between 1 and 100"

        resp = wiki_search(ctx["url"], q="anything", offset="later")
        assert resp.status_code == 400
        assert resp.json()["error"] == "offset must be >= 0"

        resp = wiki_search(ctx["url"], q="anything", limit="")
        assert resp.status_code == 400
        assert resp.json()["error"] == "limit must be between 1 and 100"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_scope_is_composed_before_collection_not_post_filtered(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="scope-order-agent")
        mine = create_project_via_api(ctx["url"], "scope-mine")
        theirs = create_project_via_api(ctx["url"], "scope-theirs")
        _, session_id = create_execution_via_api(
            ctx["url"], agent_id, "s", project_id=mine["id"]
        )

        filler = " ".join(f"word{n}" for n in range(40))
        for n in range(20):
            assert (
                wiki_put(
                    ctx["url"],
                    mine["id"],
                    f"mine-{n:02d}",
                    f"Notes {n}",
                    f"{filler} pelican {filler}",
                ).status_code
                == 201
            )
        for n in range(40):
            assert (
                wiki_put(
                    ctx["url"],
                    theirs["id"],
                    f"theirs-{n:02d}",
                    f"Pelican Pelican {n}",
                    "pelican pelican pelican pelican pelican",
                ).status_code
                == 201
            )

        unscoped = wiki_search(ctx["url"], q="pelican", limit=10)
        assert unscoped.status_code == 200
        assert [r["project_id"] for r in unscoped.json()] == [theirs["id"]] * 10

        resp = wiki_search(ctx["url"], session_id=session_id, q="pelican", limit=10)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 10
        assert all(r["project_id"] == mine["id"] for r in results)

        full = wiki_search(ctx["url"], session_id=session_id, q="pelican", limit=100)
        assert full.status_code == 200
        assert len(full.json()) == 20


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_ranks_title_matches_above_body_matches_across_projects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, alpha_session, beta, _ = two_project_world(ctx, tag="ranked")
        assert (
            wiki_put(
                ctx["url"],
                beta["id"],
                "body-match",
                "Deployment Guide",
                "kubernetes cluster config",
                tags=["ranked"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "title-match",
                "Kubernetes Setup",
                "deployment guide",
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], session_id=alpha_session, q="kubernetes")
        assert resp.status_code == 200
        results = resp.json()
        assert [r["slug"] for r in results] == ["title-match", "body-match"]
        assert results[0]["score"] > results[1]["score"]
        assert results[1]["project_id"] == beta["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_ties_break_by_project_then_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, alpha_session, beta, _ = two_project_world(ctx, tag="tied")
        assert (
            wiki_put(
                ctx["url"],
                beta["id"],
                "same-slug",
                "Tied",
                "capybara capybara",
                tags=["tied"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], alpha["id"], "same-slug", "Tied", "capybara capybara"
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], session_id=alpha_session, q="capybara")
        assert resp.status_code == 200
        ordered = [r["project_id"] for r in resp.json()]
        assert ordered == sorted([alpha["id"], beta["id"]])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_ties_within_one_project_break_by_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-slug-order")
        for slug in ["charlie", "alpha", "bravo"]:
            assert (
                wiki_put(
                    ctx["url"], project["id"], slug, "Tied", "identical wallaby text"
                ).status_code
                == 201
            )

        resp = wiki_search(ctx["url"], q="wallaby")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["alpha", "bravo", "charlie"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_result_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="shape-agent")
        project = create_project_via_api(ctx["url"], "search-shape")
        _, session_id = create_execution_via_api(
            ctx["url"], agent_id, "s", project_id=project["id"]
        )
        created = wiki_put(
            ctx["url"],
            project["id"],
            "shaped",
            "Shaped",
            "pangolin content",
            tags=["plain"],
            session_id=session_id,
        )
        assert created.status_code == 201

        resp = wiki_search(ctx["url"], q="pangolin")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        item = resp.json()[0]
        assert item["page_id"] == created.json()["id"]
        assert item["project_id"] == project["id"]
        assert item["project_slug"] == project["slug"]
        assert item["slug"] == "shaped"
        assert item["title"] == "Shaped"
        assert item["revision_number"] == 1
        assert item["tags"] == ["plain"]
        assert item["updated_by"] == session_id
        assert item["score"] > 0
        assert sorted(item) == [
            "page_id",
            "project_id",
            "project_slug",
            "revision_number",
            "score",
            "slug",
            "tags",
            "title",
            "updated_at",
            "updated_by",
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_indexed_tags_track_tag_patches(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, beta, beta_session = two_project_world(ctx, tag="indexed")
        assert (
            wiki_put(
                ctx["url"], alpha["id"], "late", "Late", "bilby content"
            ).status_code
            == 201
        )

        assert wiki_search(ctx["url"], session_id=beta_session, q="bilby").json() == []

        assert (
            httpx.patch(
                f"{ctx['url']}/api/v1/projects/{alpha['id']}/wiki/pages/late",
                json={
                    "revision_number": 1,
                    "add_tags": ["indexed"],
                    "acknowledge_share": True,
                },
                timeout=5,
            ).status_code
            == 200
        )
        found = wiki_search(ctx["url"], session_id=beta_session, q="bilby")
        assert [r["slug"] for r in found.json()] == ["late"]
        assert found.json()[0]["tags"] == ["indexed"]

        assert (
            httpx.patch(
                f"{ctx['url']}/api/v1/projects/{alpha['id']}/wiki/pages/late",
                json={"revision_number": 2, "remove_tags": ["indexed"]},
                timeout=5,
            ).status_code
            == 200
        )
        assert wiki_search(ctx["url"], session_id=beta_session, q="bilby").json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_rebuild_restores_indexed_tags(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, _, beta_session = two_project_world(ctx, tag="rebuilt")
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "shared",
                "Shared",
                "bandicoot content",
                tags=["rebuilt"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        db_url = ctx["db_url"]

    with scheduler_context(db_url=db_url) as ctx:
        found = wiki_search(ctx["url"], session_id=beta_session, q="bandicoot")
        assert found.status_code == 200
        assert [r["slug"] for r in found.json()] == ["shared"]
        assert found.json()[0]["tags"] == ["rebuilt"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_results_distinguish_colliding_slugs_by_page_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, alpha_session, beta, _ = two_project_world(ctx, tag="collide")
        theirs = wiki_put(
            ctx["url"],
            beta["id"],
            "duplicate",
            "Theirs",
            "meerkat content",
            tags=["collide"],
            acknowledge_share=True,
        )
        mine = wiki_put(ctx["url"], alpha["id"], "duplicate", "Mine", "meerkat content")
        assert theirs.status_code == 201
        assert mine.status_code == 201

        resp = wiki_search(ctx["url"], session_id=alpha_session, q="meerkat")
        assert resp.status_code == 200
        page_ids = sorted(r["page_id"] for r in resp.json())
        assert page_ids == sorted([theirs.json()["id"], mine.json()["id"]])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_operator_search_spans_all_projects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, beta, _ = two_project_world(ctx)
        assert (
            wiki_put(ctx["url"], alpha["id"], "a", "A", "ibex content").status_code
            == 201
        )
        assert (
            wiki_put(ctx["url"], beta["id"], "b", "B", "ibex content").status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="ibex")
        assert resp.status_code == 200
        assert sorted(r["slug"] for r in resp.json()) == ["a", "b"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_filter_accepts_slug_or_uuid(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, beta, _ = two_project_world(ctx)
        assert (
            wiki_put(ctx["url"], alpha["id"], "a", "A", "gecko content").status_code
            == 201
        )
        assert (
            wiki_put(ctx["url"], beta["id"], "b", "B", "gecko content").status_code
            == 201
        )

        by_uuid = wiki_search(ctx["url"], q="gecko", project=alpha["id"])
        assert by_uuid.status_code == 200
        assert [r["slug"] for r in by_uuid.json()] == ["a"]

        by_slug = wiki_search(ctx["url"], q="gecko", project=alpha["slug"])
        assert by_slug.status_code == 200
        assert [r["slug"] for r in by_slug.json()] == ["a"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_filter_rejects_an_unknown_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "filter-404")
        assert (
            wiki_put(ctx["url"], project["id"], "a", "A", "tapir content").status_code
            == 201
        )

        resp = wiki_search(ctx["url"], q="tapir", project="no-such-project")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_project_filter_narrows_within_scope_and_cannot_widen_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, beta, beta_session = two_project_world(ctx, tag="narrow")
        stranger = create_project_via_api(ctx["url"], "search-stranger")
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "shared",
                "Shared",
                "quetzal content",
                tags=["narrow"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], alpha["id"], "private", "Private", "quetzal content"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], stranger["id"], "theirs", "Theirs", "quetzal content"
            ).status_code
            == 201
        )

        narrowed = wiki_search(
            ctx["url"], session_id=beta_session, q="quetzal", project=alpha["id"]
        )
        assert narrowed.status_code == 200
        assert [r["slug"] for r in narrowed.json()] == ["shared"]

        outside = wiki_search(
            ctx["url"], session_id=beta_session, q="quetzal", project=stranger["id"]
        )
        assert outside.status_code == 200
        assert outside.json() == []

        operator = wiki_search(ctx["url"], q="quetzal")
        assert operator.status_code == 200
        assert len(operator.json()) == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_merges_shared_pages_into_the_callers_scope(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, beta, beta_session = two_project_world(ctx, tag="merged")
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "mine",
                "Mine",
                "tapir content",
                tags=["merged"],
                acknowledge_share=True,
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], alpha["id"], "hidden", "Hidden", "tapir content"
            ).status_code
            == 201
        )

        resp = wiki_search(ctx["url"], session_id=beta_session, q="tapir")
        assert resp.status_code == 200
        assert [r["slug"] for r in resp.json()] == ["mine"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_results_carry_the_current_project_slug_after_a_rename(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-renamed")
        assert (
            wiki_put(
                ctx["url"], project["id"], "doc", "Doc", "numbat content"
            ).status_code
            == 201
        )
        assert (
            httpx.patch(
                f"{ctx['url']}/api/v1/projects/{project['id']}",
                json={"slug": "search-renamed-later"},
                timeout=5,
            ).status_code
            == 200
        )

        resp = wiki_search(ctx["url"], q="numbat")
        assert resp.status_code == 200
        assert resp.json()[0]["project_id"] == project["id"]
        assert resp.json()[0]["project_slug"] == "search-renamed-later"

        narrowed = wiki_search(ctx["url"], q="numbat", project="search-renamed-later")
        assert narrowed.status_code == 200
        assert [r["slug"] for r in narrowed.json()] == ["doc"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_index_entry_is_returned_but_opening_it_is_denied(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        alpha, _, beta, beta_session = two_project_world(ctx, tag="fleeting")
        assert (
            wiki_put(
                ctx["url"],
                alpha["id"],
                "vanishing",
                "Vanishing",
                "dugong content",
                tags=["fleeting"],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        found = wiki_search(ctx["url"], session_id=beta_session, q="dugong")
        assert found.status_code == 200
        assert [r["slug"] for r in found.json()] == ["vanishing"]

        untag_page_in_db(ctx["db_url"], alpha["id"], "vanishing", "fleeting")

        stale = wiki_search(ctx["url"], session_id=beta_session, q="dugong")
        assert stale.status_code == 200
        assert [r["slug"] for r in stale.json()] == ["vanishing"]
        assert stale.json()[0]["title"] == "Vanishing"
        assert "body" not in stale.json()[0]

        assert (
            wiki_get(
                ctx["url"], alpha["id"], "vanishing", session_id=beta_session
            ).status_code
            == 404
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_excludes_deleted_pages(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-deleted")
        assert (
            wiki_put(
                ctx["url"], project["id"], "doomed", "Doomed", "ocelot content"
            ).status_code
            == 201
        )
        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/doomed",
                timeout=5,
            ).status_code
            == 204
        )

        resp = wiki_search(ctx["url"], q="ocelot")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_search_reflects_the_current_revision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "search-current")
        assert (
            wiki_put(
                ctx["url"], project["id"], "churn", "Churn", "originaltext marmot"
            ).status_code
            == 201
        )
        assert (
            httpx.patch(
                f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages/churn",
                json={
                    "revision_number": 1,
                    "edits": [
                        {"old_string": "originaltext", "new_string": "replacementtext"}
                    ],
                },
                timeout=5,
            ).status_code
            == 200
        )

        resp = wiki_search(ctx["url"], q="marmot")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["revision_number"] == 2

        stale = wiki_search(ctx["url"], q="originaltext")
        assert stale.status_code == 200
        assert stale.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_paging_is_not_bounded_by_an_internal_candidate_cap(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "deep-paging")
        total = 120
        for n in range(total):
            assert (
                wiki_put(
                    ctx["url"],
                    project["id"],
                    f"p{n:03d}",
                    f"Page {n}",
                    "tardigrade content",
                ).status_code
                == 201
            )

        seen = []
        for offset in range(0, total, 100):
            resp = wiki_search(ctx["url"], q="tardigrade", limit=100, offset=offset)
            assert resp.status_code == 200, resp.text
            seen.extend(r["slug"] for r in resp.json())

        assert len(seen) == total
        assert len(set(seen)) == total, "paging repeated or dropped a result"

        past = wiki_search(ctx["url"], q="tardigrade", limit=10, offset=total)
        assert past.status_code == 200
        assert past.json() == []
