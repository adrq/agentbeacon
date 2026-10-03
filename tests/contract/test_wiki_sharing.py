# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import pathlib
import uuid

import httpx
import pytest

from tests.mock_agent_helpers import db_conn, set_execution_fields, set_session_fields
from tests.testhelpers import (
    create_project_via_api,
    scheduler_context,
)
from tests.wiki_helpers import (
    CROSS_PROJECT_WRITES_FLAG,
    ERR_CROSS_PROJECT_COLLECTION,
    ERR_CROSS_PROJECT_CREATE,
    ERR_NOT_PAGE_OWNER,
    ERR_READ_ONLY_MEMBER,
    SHARE_TAG,
    bearer,
    build_world,
    materialize_page,
    make_share_tag,
    set_config_flag,
    wiki_get,
    wiki_patch,
    wiki_put,
    wiki_search,
)


CALLERS = ["owner", "read_member", "write_member", "outsider", "anon"]
TARGETS = ["own", "co_member_tagged", "co_member_untagged", "non_member"]
VERBS = [
    "get",
    "revisions",
    "revision_detail",
    "list",
    "changes",
    "tags",
    "export",
    "subscriptions_list",
    "subscriptions_create",
    "create",
    "patch_edits",
    "patch_title",
    "patch_tags",
    "delete",
]

TARGET_HOST = {
    "owner": {
        "own": "owner",
        "co_member_tagged": "write_member",
        "co_member_untagged": "write_member",
        "non_member": "outsider",
    },
    "read_member": {
        "own": "read_member",
        "co_member_tagged": "owner",
        "co_member_untagged": "owner",
        "non_member": "outsider",
    },
    "write_member": {
        "own": "write_member",
        "co_member_tagged": "owner",
        "co_member_untagged": "owner",
        "non_member": "outsider",
    },
    "outsider": {
        "own": "outsider",
        "co_member_tagged": "owner",
        "co_member_untagged": "owner",
        "non_member": "write_member",
    },
    "anon": {
        "own": "owner",
        "co_member_tagged": "owner",
        "co_member_untagged": "owner",
        "non_member": "outsider",
    },
}

_READ_RULE = {
    "own": {"*": (200, None)},
    "co_member_tagged": {"*": (200, None), "outsider": (404, None)},
    "co_member_untagged": {"*": (404, None), "anon": (200, None)},
    "non_member": {"*": (404, None), "anon": (200, None)},
}

_COLLECTION_RULE = {
    "own": {"*": (200, None)},
    "co_member_tagged": {
        "*": (403, ERR_CROSS_PROJECT_COLLECTION),
        "anon": (200, None),
    },
    "co_member_untagged": {
        "*": (403, ERR_CROSS_PROJECT_COLLECTION),
        "anon": (200, None),
    },
    "non_member": {"*": (403, ERR_CROSS_PROJECT_COLLECTION), "anon": (200, None)},
}

_CROSS_PROJECT_WRITE_RULE = {
    "own": {"*": (200, None)},
    "co_member_tagged": {
        "*": (200, None),
        "read_member": (403, ERR_READ_ONLY_MEMBER),
        "outsider": (404, None),
    },
    "co_member_untagged": {"*": (404, None), "anon": (200, None)},
    "non_member": {"*": (404, None), "anon": (200, None)},
}

_OWNER_ONLY_RULE = {
    "own": {"*": (200, None)},
    "co_member_tagged": {
        "*": (403, ERR_NOT_PAGE_OWNER),
        "outsider": (404, None),
        "anon": (200, None),
    },
    "co_member_untagged": {"*": (404, None), "anon": (200, None)},
    "non_member": {"*": (404, None), "anon": (200, None)},
}

_CREATED_COLLECTION_RULE = {
    target: {
        caller: ((201, None) if outcome == (200, None) else outcome)
        for caller, outcome in rule.items()
    }
    for target, rule in _COLLECTION_RULE.items()
}

POLICY = {
    "get": _READ_RULE,
    "revisions": _READ_RULE,
    "revision_detail": _READ_RULE,
    "list": _COLLECTION_RULE,
    "changes": _COLLECTION_RULE,
    "tags": _COLLECTION_RULE,
    "export": _COLLECTION_RULE,
    "subscriptions_list": _COLLECTION_RULE,
    "subscriptions_create": _CREATED_COLLECTION_RULE,
    "create": {
        "own": {"*": (201, None)},
        "co_member_tagged": {
            "*": (403, ERR_CROSS_PROJECT_CREATE),
            "anon": (201, None),
        },
        "co_member_untagged": {
            "*": (403, ERR_CROSS_PROJECT_CREATE),
            "anon": (201, None),
        },
        "non_member": {"*": (403, ERR_CROSS_PROJECT_CREATE), "anon": (201, None)},
    },
    "patch_edits": _CROSS_PROJECT_WRITE_RULE,
    "patch_title": _CROSS_PROJECT_WRITE_RULE,
    "patch_tags": _OWNER_ONLY_RULE,
    "delete": {
        "own": {"*": (204, None)},
        "co_member_tagged": {
            "*": (403, ERR_NOT_PAGE_OWNER),
            "outsider": (404, None),
            "anon": (204, None),
        },
        "co_member_untagged": {"*": (404, None), "anon": (204, None)},
        "non_member": {"*": (404, None), "anon": (204, None)},
    },
}


def expected_for(verb, caller, target):
    rule = POLICY[verb][target]
    return rule.get(caller, rule["*"])


def issue(url, verb, project_id, slug, session_id):
    headers = bearer(session_id)
    base = f"{url}/api/v1/projects/{project_id}/wiki/pages/{slug}"
    if verb == "get":
        return wiki_get(url, project_id, slug, session_id=session_id)
    if verb == "revisions":
        return httpx.get(f"{base}/revisions", headers=headers, timeout=5)
    if verb == "revision_detail":
        return httpx.get(f"{base}/revisions/1", headers=headers, timeout=5)
    if verb == "list":
        return httpx.get(
            f"{url}/api/v1/projects/{project_id}/wiki/pages", headers=headers, timeout=5
        )
    if verb in ("changes", "tags", "export"):
        return httpx.get(
            f"{url}/api/v1/projects/{project_id}/wiki/{verb}",
            headers=headers,
            timeout=5,
        )
    if verb == "subscriptions_list":
        return httpx.get(
            f"{url}/api/v1/projects/{project_id}/wiki/subscriptions",
            params={"subscriber": "matrix-agent"},
            headers=headers,
            timeout=5,
        )
    if verb == "subscriptions_create":
        return httpx.post(
            f"{url}/api/v1/projects/{project_id}/wiki/subscriptions",
            json={"subscriber": "matrix-agent", "page_slug": slug},
            headers=headers,
            timeout=5,
        )
    if verb == "create":
        return wiki_put(
            url, project_id, slug, "Created", "fresh body", session_id=session_id
        )
    if verb == "patch_edits":
        return wiki_patch(
            url,
            project_id,
            slug,
            2,
            edits=[{"old_string": "matrix body", "new_string": "edited body"}],
            session_id=session_id,
        )
    if verb == "patch_title":
        return wiki_patch(
            url,
            project_id,
            slug,
            2,
            title={"old": "Matrix Page", "new": "Retitled Page"},
            session_id=session_id,
        )
    if verb == "patch_tags":
        return wiki_patch(
            url, project_id, slug, 2, add_tags=["matrix-extra"], session_id=session_id
        )
    if verb == "delete":
        return httpx.delete(base, headers=headers, timeout=5)
    raise ValueError(f"unknown verb: {verb}")


MUTATION_SUCCESS = {
    "subscriptions_create": 201,
    "create": 201,
    "patch_edits": 200,
    "patch_title": 200,
    "patch_tags": 200,
    "delete": 204,
}


def page_state(url, project_id, slug, owner_session):
    resp = wiki_get(url, project_id, slug, session_id=owner_session)
    if resp.status_code == 404:
        return None
    assert resp.status_code == 200, f"reading back {slug} as its owner: {resp.text}"
    page = resp.json()
    return {
        "title": page["title"],
        "body": page["body"],
        "revision_number": page["revision_number"],
        "tags": sorted(page.get("tags") or []),
    }


def subscription_state(url, project_id, owner_session):
    resp = httpx.get(
        f"{url}/api/v1/projects/{project_id}/wiki/subscriptions",
        params={"subscriber": "matrix-agent"},
        headers=bearer(owner_session),
        timeout=5,
    )
    assert resp.status_code == 200, f"listing subscriptions: {resp.text}"
    return sorted((s.get("page_slug"), s.get("tag_name")) for s in resp.json())


def subscription_rows(db_url, project_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT id, subscriber, page_slug, tag_name FROM wiki_subscriptions "
            "WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
    return [tuple(r) for r in rows]


def membership_state(url, tag_id):
    resp = httpx.get(f"{url}/api/v1/wiki/tags", timeout=5)
    assert resp.status_code == 200, resp.text
    entry = [t for t in resp.json() if t["tag_id"] == tag_id]
    assert entry, f"tag {tag_id} not found"
    return {m["project_id"]: m["access_level"] for m in entry[0]["members"]}


def observe(ctx, verb, project_id, slug, owner_session):
    if verb == "subscriptions_create":
        return subscription_state(ctx["url"], project_id, owner_session)
    return page_state(ctx["url"], project_id, slug, owner_session)


def run_matrix(ctx):
    projects, sessions, _ = build_world(ctx)

    failures = []
    row = 0
    for verb in VERBS:
        for caller in CALLERS:
            for target in TARGETS:
                row += 1
                host = TARGET_HOST[caller][target]
                project_id = projects[host]["id"]
                slug = f"mx-{row}"
                if verb not in (
                    "list",
                    "changes",
                    "tags",
                    "export",
                    "subscriptions_list",
                    "subscriptions_create",
                    "create",
                ):
                    materialize_page(
                        ctx["url"],
                        project_id,
                        slug,
                        tagged=(target == "co_member_tagged"),
                        session_id=sessions[host],
                    )
                exp_status, exp_error = expected_for(verb, caller, target)

                denied = (
                    verb in MUTATION_SUCCESS and exp_status != MUTATION_SUCCESS[verb]
                )
                before = (
                    observe(ctx, verb, project_id, slug, sessions[host])
                    if denied
                    else None
                )

                resp = issue(ctx["url"], verb, project_id, slug, sessions[caller])

                if resp.status_code != exp_status:
                    failures.append(
                        f"{verb} caller={caller} target={target}: "
                        f"expected {exp_status}, got {resp.status_code} ({resp.text})"
                    )
                elif exp_error is not None and resp.json().get("error") != exp_error:
                    failures.append(
                        f"{verb} caller={caller} target={target}: "
                        f"expected error {exp_error!r}, got {resp.json().get('error')!r}"
                    )

                if denied:
                    after = observe(ctx, verb, project_id, slug, sessions[host])
                    if after != before:
                        failures.append(
                            f"{verb} caller={caller} target={target}: refused with "
                            f"{resp.status_code} but the state CHANGED: "
                            f"{before!r} -> {after!r}"
                        )
    return failures


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_authorization_matrix(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        failures = run_matrix(ctx)
        assert failures == []


def operator_routes(ctx, projects, tag_id):
    base = ctx["url"]
    member = projects["read_member"]["id"]
    return [
        ("GET", f"{base}/api/v1/wiki/tags", None),
        (
            "POST",
            f"{base}/api/v1/wiki/tags/{tag_id}/members",
            {"project": projects["outsider"]["id"], "access_level": "read"},
        ),
        (
            "PATCH",
            f"{base}/api/v1/wiki/tags/{tag_id}/members/{member}",
            {"access_level": "read_write"},
        ),
        ("DELETE", f"{base}/api/v1/wiki/tags/{tag_id}/members/{member}", None),
    ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_operator_routes_reject_agent_tokens(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)

        for method, url, body in operator_routes(ctx, projects, tag_id):
            resp = httpx.request(
                method,
                url,
                json=body,
                headers=bearer(sessions["owner"]),
                timeout=5,
            )
            assert resp.status_code == 403, f"{method} {url}"
            assert resp.json()["error"] == "operator_scope_only"

        entry = [
            t
            for t in httpx.get(f"{ctx['url']}/api/v1/wiki/tags", timeout=5).json()
            if t["tag_id"] == tag_id
        ][0]
        assert len(entry["members"]) == 3
        levels = {m["project_id"]: m["access_level"] for m in entry["members"]}
        assert levels == {
            projects["owner"]["id"]: "read_write",
            projects["read_member"]["id"]: "read",
            projects["write_member"]["id"]: "read_write",
        }, levels


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_operator_routes_permit_an_unauthenticated_caller(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        build_world(ctx)

        assert httpx.get(f"{ctx['url']}/api/v1/wiki/tags", timeout=5).status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_operator_routes_reject_malformed_tokens(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)

        before = membership_state(ctx["url"], tag_id)

        for headers in [
            {"Authorization": "NotBearer abc"},
            {"Authorization": f"Bearer {uuid.uuid4()}"},
        ]:
            for method, url, body in operator_routes(ctx, projects, tag_id):
                resp = httpx.request(method, url, json=body, headers=headers, timeout=5)
                assert resp.status_code == 401, f"{method} {url}"
                after = membership_state(ctx["url"], tag_id)
                assert after == before, (
                    f"{method} {url} refused with 401 but membership CHANGED: "
                    f"{before} -> {after}"
                )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_malformed_and_unknown_tokens_are_401_on_every_read_path(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "guarded", tagged=True)
        owner_project = projects["owner"]["id"]

        set_session_fields(ctx["db_url"], sessions["read_member"], outcome="completed")
        bad_headers = [
            {"Authorization": "NotBearer abc"},
            {"Authorization": "Bearer"},
            {"Authorization": f"Bearer {uuid.uuid4()}"},
            {"Authorization": f"Bearer {sessions['read_member']}"},
        ]
        paths = [
            (f"/api/v1/projects/{owner_project}/wiki/pages", {}),
            (f"/api/v1/projects/{owner_project}/wiki/pages/guarded", {}),
            (f"/api/v1/projects/{owner_project}/wiki/pages/guarded/revisions", {}),
            (f"/api/v1/projects/{owner_project}/wiki/pages/guarded/revisions/1", {}),
            (f"/api/v1/projects/{owner_project}/wiki/changes", {}),
            (f"/api/v1/projects/{owner_project}/wiki/tags", {}),
            (f"/api/v1/projects/{owner_project}/wiki/export", {}),
            ("/api/v1/wiki/search", {"q": "matrix"}),
        ]
        for headers in bad_headers:
            for path, params in paths:
                resp = httpx.get(
                    f"{ctx['url']}{path}", params=params, headers=headers, timeout=5
                )
                assert resp.status_code == 401, (
                    f"{path} with {headers['Authorization']!r}"
                )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_malformed_token_is_401_on_write_paths(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "guarded", tagged=False)
        stranger = str(uuid.uuid4())
        owner_session = sessions["owner"]

        assert page_state(ctx["url"], owner, "new-page", owner_session) is None
        assert (
            wiki_put(
                ctx["url"],
                owner,
                "new-page",
                "T",
                "b",
                session_id=stranger,
            ).status_code
            == 401
        )
        assert page_state(ctx["url"], owner, "new-page", owner_session) is None, (
            "refused with 401 but the page was created"
        )

        before = page_state(ctx["url"], owner, "guarded", owner_session)
        assert (
            wiki_patch(
                ctx["url"],
                owner,
                "guarded",
                2,
                edits=[{"old_string": "matrix", "new_string": "x"}],
                session_id=stranger,
            ).status_code
            == 401
        )
        after = page_state(ctx["url"], owner, "guarded", owner_session)
        assert after == before, (
            f"refused with 401 but the page CHANGED: {before} -> {after}"
        )

        assert (
            httpx.delete(
                f"{ctx['url']}/api/v1/projects/{owner}/wiki/pages/guarded",
                headers=bearer(stranger),
                timeout=5,
            ).status_code
            == 401
        )
        assert page_state(ctx["url"], owner, "guarded", owner_session) == before, (
            "refused with 401 but the page was deleted"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_on_an_existing_slug_is_still_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "put-collision")
        assert (
            wiki_put(ctx["url"], project["id"], "doc", "Doc", "body").status_code == 201
        )

        resp = wiki_put(ctx["url"], project["id"], "doc", "Other", "other body")
        assert resp.status_code == 409
        assert resp.json()["error"] == "slug_exists"
        assert resp.json()["current_page"]["body"] == "body"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_write_bodies_reject_unknown_fields(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "strict-bodies")
        assert (
            wiki_put(ctx["url"], project["id"], "doc", "Doc", "body text").status_code
            == 201
        )
        pages = f"{ctx['url']}/api/v1/projects/{project['id']}/wiki/pages"

        resp = httpx.patch(
            f"{pages}/doc",
            json={
                "revision_number": 1,
                "add_tags": ["real-change"],
                "body": "wholesale replacement",
            },
            timeout=5,
        )
        assert resp.status_code == 400
        page = wiki_get(ctx["url"], project["id"], "doc")
        assert page.json()["body"] == "body text"
        assert page.json()["tags"] == []
        assert page.json()["revision_number"] == 1

        resp = httpx.patch(
            f"{pages}/doc",
            json={
                "revision_number": 1,
                "edits": [{"old_string": "body", "new_string": "edited"}],
                "tags": ["replacement-set"],
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert wiki_get(ctx["url"], project["id"], "doc").json()["body"] == "body text"

        resp = httpx.patch(
            f"{pages}/doc",
            json={
                "revision_number": 1,
                "title": {"old": "Doc", "new": "Renamed", "invented": True},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert wiki_get(ctx["url"], project["id"], "doc").json()["title"] == "Doc"

        resp = httpx.put(
            f"{pages}/new-page",
            json={"title": "T", "body": "b", "revision_number": 1},
            timeout=5,
        )
        assert resp.status_code == 400
        assert wiki_get(ctx["url"], project["id"], "new-page").status_code == 404

        resp = httpx.post(
            f"{ctx['url']}/api/v1/wiki/tags/{uuid.uuid4()}/members",
            json={
                "project": project["id"],
                "access_level": "read",
                "acknowledged": True,
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_put_accepts_tags_at_birth(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "put-tags-at-birth")
        resp = wiki_put(
            ctx["url"], project["id"], "doc", "Doc", "body", tags=["plain", "other"]
        )
        assert resp.status_code == 201
        assert sorted(resp.json()["tags"]) == ["other", "plain"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unshared_pages_are_indistinguishable_from_missing_ones(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "untagged", tagged=False)
        materialize_page(
            ctx["url"], projects["outsider"]["id"], "stranger", tagged=False
        )
        caller = sessions["read_member"]

        missing = wiki_get(
            ctx["url"], projects["owner"]["id"], "no-such-page", session_id=caller
        )
        assert missing.status_code == 404

        for project_id, slug, label in [
            (projects["owner"]["id"], "untagged", "untagged in a co-member project"),
            (projects["outsider"]["id"], "stranger", "page in a non-member project"),
        ]:
            denied = wiki_get(ctx["url"], project_id, slug, session_id=caller)
            assert denied.status_code == missing.status_code, label
            assert denied.json() == missing.json(), label

        for suffix in ["/revisions", "/revisions/1"]:
            absent = httpx.get(
                f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
                f"/wiki/pages/no-such-page{suffix}",
                headers=bearer(caller),
                timeout=5,
            )
            denied = httpx.get(
                f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
                f"/wiki/pages/untagged{suffix}",
                headers=bearer(caller),
                timeout=5,
            )
            assert denied.status_code == absent.status_code, suffix
            assert denied.json() == absent.json(), suffix

        edits = [{"old_string": "matrix body", "new_string": "x"}]
        absent = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "no-such-page",
            2,
            edits=edits,
            session_id=caller,
        )
        denied = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "untagged",
            2,
            edits=edits,
            session_id=caller,
        )
        assert absent.status_code == 404
        assert denied.status_code == absent.status_code
        assert denied.json() == absent.json()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cross_project_writes_are_enabled_without_configuration(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "default-on", tagged=True)

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "default-on",
            2,
            edits=[{"old_string": "matrix body", "new_string": "member body"}],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cross_project_write_denied_while_disabled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "flag-off", tagged=True)
        assert (
            set_config_flag(ctx["url"], CROSS_PROJECT_WRITES_FLAG, False).status_code
            == 200
        )

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "flag-off",
            2,
            edits=[{"old_string": "matrix body", "new_string": "sneaky"}],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "cross_project_writes_disabled"

        page = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "flag-off",
            session_id=sessions["write_member"],
        )
        assert page.status_code == 200
        assert page.json()["body"] == "matrix body text"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_effective_access_downgraded_while_disabled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "effective", tagged=True)
        assert (
            set_config_flag(ctx["url"], CROSS_PROJECT_WRITES_FLAG, False).status_code
            == 200
        )

        resp = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "effective",
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200
        assert resp.json()["access"] == "read"

        assert (
            set_config_flag(ctx["url"], CROSS_PROJECT_WRITES_FLAG, True).status_code
            == 200
        )
        resp = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "effective",
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200
        assert resp.json()["access"] == "read_write"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_invalid_kill_switch_value_falls_back_to_enabled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "typo", tagged=True)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/config",
            json={"name": CROSS_PROJECT_WRITES_FLAG, "value": "no"},
            timeout=5,
        )
        assert resp.status_code == 200

        page = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "typo",
            session_id=sessions["write_member"],
        )
        assert page.status_code == 200
        assert page.json()["access"] == "read_write"

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "typo",
            2,
            edits=[{"old_string": "matrix body", "new_string": "member body"}],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200

        log = pathlib.Path(ctx["process"].log_file.name).read_text()
        assert f"config '{CROSS_PROJECT_WRITES_FLAG}' has invalid value 'no'" in log
        assert (
            f"config '{CROSS_PROJECT_WRITES_FLAG}' has invalid value 'no' in transaction"
            in log
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_own_pages_always_report_read_write_access(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "mine", tagged=False)

        resp = wiki_get(
            ctx["url"], projects["owner"]["id"], "mine", session_id=sessions["owner"]
        )
        assert resp.status_code == 200
        assert resp.json()["access"] == "read_write"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_access_present_on_listing_absent_on_search(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "sourced", tagged=True)

        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/pages",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert listing.status_code == 200
        shared = [p for p in listing.json() if p["slug"] == "sourced"]
        assert len(shared) == 1
        assert shared[0]["access"] == "read"

        searched = wiki_search(
            ctx["url"], session_id=sessions["read_member"], q="sourced"
        )
        assert searched.status_code == 200
        assert len(searched.json()) == 1
        assert "access" not in searched.json()[0]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_items_carry_owning_project_and_page_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "provenance", tagged=True)

        listing = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/pages",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert listing.status_code == 200
        item = [p for p in listing.json() if p["slug"] == "provenance"][0]
        assert item["project_id"] == projects["owner"]["id"]
        assert item["project_slug"] == projects["owner"]["slug"]

        page = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "provenance",
            session_id=sessions["read_member"],
        )
        assert item["page_id"] == page.json()["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_anonymous_global_search_is_permitted(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "findable", tagged=True)

        assert wiki_search(ctx["url"], q="matrix").status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_routes_follow_the_auth_edges(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        own = projects["owner"]["id"]
        subs = f"{ctx['url']}/api/v1/projects/{own}/wiki/subscriptions"

        created = httpx.post(
            subs,
            json={"subscriber": "owner-agent", "page_slug": "anything"},
            headers=bearer(sessions["owner"]),
            timeout=5,
        )
        assert created.status_code == 201
        sub_id = created.json()["id"]

        before = subscription_rows(ctx["db_url"], own)

        for headers in [
            {"Authorization": "NotBearer abc"},
            {"Authorization": f"Bearer {uuid.uuid4()}"},
        ]:
            assert (
                httpx.get(
                    subs,
                    params={"subscriber": "owner-agent"},
                    headers=headers,
                    timeout=5,
                ).status_code
                == 401
            )
            assert (
                httpx.post(
                    subs,
                    json={"subscriber": "x", "page_slug": "y"},
                    headers=headers,
                    timeout=5,
                ).status_code
                == 401
            )
            after = subscription_rows(ctx["db_url"], own)
            assert after == before, (
                f"POST refused with 401 but subscriptions CHANGED: {before} -> {after}"
            )

            assert (
                httpx.delete(f"{subs}/{sub_id}", headers=headers, timeout=5).status_code
                == 401
            )
            after = subscription_rows(ctx["db_url"], own)
            assert after == before, (
                f"DELETE refused with 401 but subscriptions CHANGED: {before} -> {after}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_merged_listing_includes_shared_pages_and_excludes_untagged(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "published", tagged=True)
        materialize_page(ctx["url"], projects["owner"]["id"], "internal", tagged=False)
        materialize_page(
            ctx["url"], projects["read_member"]["id"], "reader-home", tagged=False
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/pages",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        slugs = sorted(p["slug"] for p in resp.json())
        assert slugs == ["published", "reader-home"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_collision_across_members_shows_both(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "contract", tagged=True)
        materialize_page(
            ctx["url"], projects["read_member"]["id"], "contract", tagged=False
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/pages",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        colliding = [p for p in resp.json() if p["slug"] == "contract"]
        assert len(colliding) == 2
        owners = sorted(p["project_id"] for p in colliding)
        assert owners == sorted(
            [projects["owner"]["id"], projects["read_member"]["id"]]
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_changes_feed_merges_own_and_shared_and_excludes_the_rest(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "shared", tagged=True)
        materialize_page(ctx["url"], projects["owner"]["id"], "private", tagged=False)
        materialize_page(
            ctx["url"], projects["outsider"]["id"], "stranger", tagged=False
        )
        materialize_page(
            ctx["url"], projects["read_member"]["id"], "mine", tagged=False
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/changes",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        changes = resp.json()
        assert sorted(c["slug"] for c in changes) == ["mine", "shared"]
        by_slug = {c["slug"]: c for c in changes}
        assert by_slug["shared"]["project_id"] == projects["owner"]["id"]
        assert by_slug["shared"]["project_slug"] == projects["owner"]["slug"]
        assert by_slug["mine"]["project_id"] == projects["read_member"]["id"]
        assert [c["created_at"] for c in changes] == sorted(
            (c["created_at"] for c in changes), reverse=True
        )

        assert (
            wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "shared",
                2,
                remove_tags=[SHARE_TAG],
            ).status_code
            == 200
        )
        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/changes",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert [c["slug"] for c in resp.json()] == ["mine"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tags_endpoint_lists_share_tags_no_visible_page_carries(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"],
                projects["read_member"]["id"],
                "reader-home",
                "Local",
                "local body",
                tags=["local-only"],
            ).status_code
            == 201
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/tags",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        tags = {t["name"]: t for t in resp.json()}
        assert sorted(tags) == [SHARE_TAG, "local-only"]
        assert tags[SHARE_TAG]["shared"] is True
        assert tags[SHARE_TAG]["page_count"] == 0
        assert tags["local-only"]["shared"] is False
        assert tags["local-only"]["page_count"] == 1

        for host, slug in [
            (projects["owner"]["id"], "theirs-one"),
            (projects["owner"]["id"], "theirs-two"),
            (projects["read_member"]["id"], "ours"),
        ]:
            assert (
                wiki_put(
                    ctx["url"],
                    host,
                    slug,
                    "Doc",
                    "body",
                    tags=[SHARE_TAG],
                    acknowledge_share=True,
                ).status_code
                == 201
            )
        assert (
            wiki_put(
                ctx["url"],
                projects["owner"]["id"],
                "invisible",
                "Doc",
                "body",
                tags=["local-only"],
            ).status_code
            == 201
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/tags",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        tags = {t["name"]: t for t in resp.json()}
        assert tags[SHARE_TAG]["page_count"] == 3
        assert tags["local-only"]["page_count"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_tags_endpoint_carries_tag_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/tags",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        tags = {t["name"]: t for t in resp.json()}
        assert tags[SHARE_TAG]["tag_id"] == tag_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscriptions_are_own_project_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "published", tagged=True)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}/wiki/subscriptions",
            json={"subscriber": "reader-agent", "page_slug": "published"},
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == ERR_CROSS_PROJECT_COLLECTION


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_subscription_delete_is_own_project_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "published", tagged=True)

        created = httpx.post(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}/wiki/subscriptions",
            json={"subscriber": "owner-agent", "page_slug": "published"},
            headers=bearer(sessions["owner"]),
            timeout=5,
        )
        assert created.status_code == 201
        sub_id = created.json()["id"]

        resp = httpx.delete(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
            f"/wiki/subscriptions/{sub_id}",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == ERR_CROSS_PROJECT_COLLECTION

        still_there = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}/wiki/subscriptions",
            params={"subscriber": "owner-agent"},
            headers=bearer(sessions["owner"]),
            timeout=5,
        )
        assert len(still_there.json()) == 1

        removed = httpx.delete(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
            f"/wiki/subscriptions/{sub_id}",
            headers=bearer(sessions["owner"]),
            timeout=5,
        )
        assert removed.status_code == 204


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_export_is_own_project_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "published", tagged=True)
        materialize_page(
            ctx["url"], projects["read_member"]["id"], "reader-home", tagged=False
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['read_member']['id']}/wiki/export",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        assert [p["slug"] for p in resp.json()] == ["reader-home"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_shared_page_history_is_live_not_historical(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"],
                projects["owner"]["id"],
                "sanitised",
                "Draft",
                "secret first draft",
            ).status_code
            == 201
        )
        assert (
            wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "sanitised",
                1,
                edits=[
                    {"old_string": "secret first draft", "new_string": "cleaned up"}
                ],
                add_tags=[SHARE_TAG],
                acknowledge_share=True,
            ).status_code
            == 200
        )

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
            f"/wiki/pages/sanitised/revisions/1",
            headers=bearer(sessions["read_member"]),
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "secret first draft"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_publication_confirmation_states_history_travels(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "warned", "Warned", "body"
            ).status_code
            == 201
        )

        resp = wiki_patch(
            ctx["url"], projects["owner"]["id"], "warned", 1, add_tags=[SHARE_TAG]
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "share_tag_requires_confirmation"
        assert data["warning"] == "all history travels with the page"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_share_tag_without_acknowledgment_returns_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "to-publish", "Doc", "body"
            ).status_code
            == 201
        )

        resp = wiki_patch(
            ctx["url"], projects["owner"]["id"], "to-publish", 1, add_tags=[SHARE_TAG]
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "share_tag_requires_confirmation"
        assert len(data["publishes"]) == 1
        entry = data["publishes"][0]
        assert entry["tag"] == SHARE_TAG
        shares_with = {s["project"]: s["access"] for s in entry["shares_with"]}
        assert shares_with == {
            projects["read_member"]["slug"]: "read",
            projects["write_member"]["slug"]: "read_write",
        }
        assert "acknowledge_share" in data["remedy"]

        page = wiki_get(ctx["url"], projects["owner"]["id"], "to-publish")
        assert page.json()["tags"] == []
        assert page.json()["revision_number"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_acknowledge_share_accepted_up_front(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "intended", "Doc", "body"
            ).status_code
            == 201
        )

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "intended",
            1,
            add_tags=[SHARE_TAG],
            acknowledge_share=True,
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == [SHARE_TAG]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_confirmation_reports_slug_conflicts(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "contract", "Doc", "body"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"],
                projects["read_member"]["id"],
                "contract",
                "Theirs",
                "b",
                tags=[SHARE_TAG],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        resp = wiki_patch(
            ctx["url"], projects["owner"]["id"], "contract", 1, add_tags=[SHARE_TAG]
        )
        assert resp.status_code == 409
        assert resp.json()["publishes"][0]["slug_conflicts"] == [
            {"project": projects["read_member"]["slug"], "slug": "contract"}
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_confirmation_lists_every_tag_the_request_publishes_into(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        make_share_tag(
            ctx["url"],
            "second-contract",
            {
                projects["owner"]["id"]: "read_write",
                projects["outsider"]["id"]: "read",
            },
        )
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "multi", "Multi", "body"
            ).status_code
            == 201
        )

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "multi",
            1,
            add_tags=[SHARE_TAG, "second-contract"],
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "share_tag_requires_confirmation"

        by_tag = {e["tag"]: e for e in data["publishes"]}
        assert sorted(by_tag) == sorted([SHARE_TAG, "second-contract"])
        assert {
            s["project"]: s["access"] for s in by_tag[SHARE_TAG]["shares_with"]
        } == {
            projects["read_member"]["slug"]: "read",
            projects["write_member"]["slug"]: "read_write",
        }
        assert {
            s["project"]: s["access"] for s in by_tag["second-contract"]["shares_with"]
        } == {projects["outsider"]["slug"]: "read"}

        page = wiki_get(ctx["url"], projects["owner"]["id"], "multi")
        assert page.json()["tags"] == []

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "multi",
            1,
            add_tags=[SHARE_TAG, "second-contract"],
            acknowledge_share=True,
        )
        assert resp.status_code == 200
        assert sorted(resp.json()["tags"]) == sorted([SHARE_TAG, "second-contract"])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_untagged_same_slug_page_is_not_a_conflict(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "secret", "Mine", "body"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"], projects["read_member"]["id"], "secret", "Theirs", "b"
            ).status_code
            == 201
        )
        assert (
            wiki_get(
                ctx["url"],
                projects["read_member"]["id"],
                "secret",
                session_id=sessions["owner"],
            ).status_code
            == 404
        )

        resp = wiki_patch(
            ctx["url"], projects["owner"]["id"], "secret", 1, add_tags=[SHARE_TAG]
        )
        assert resp.status_code == 409
        entry = resp.json()["publishes"][0]
        assert entry["tag"] == SHARE_TAG
        assert [m["project"] for m in entry["shares_with"]] == sorted(
            [
                projects["read_member"]["slug"],
                projects["write_member"]["slug"],
            ]
        )
        assert entry["slug_conflicts"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_conflicts_are_reported_against_the_right_tag(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        make_share_tag(
            ctx["url"],
            "second-contract",
            {
                projects["owner"]["id"]: "read_write",
                projects["outsider"]["id"]: "read",
            },
        )
        assert (
            wiki_put(
                ctx["url"], projects["owner"]["id"], "contract", "Doc", "body"
            ).status_code
            == 201
        )
        assert (
            wiki_put(
                ctx["url"],
                projects["read_member"]["id"],
                "contract",
                "Theirs",
                "b",
                tags=[SHARE_TAG],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "contract",
            1,
            add_tags=[SHARE_TAG, "second-contract"],
        )
        assert resp.status_code == 409
        by_tag = {e["tag"]: e for e in resp.json()["publishes"]}
        assert by_tag[SHARE_TAG]["slug_conflicts"] == [
            {"project": projects["read_member"]["slug"], "slug": "contract"}
        ]
        assert by_tag["second-contract"]["slug_conflicts"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_with_several_share_tags_lists_them_all(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        make_share_tag(
            ctx["url"],
            "second-contract",
            {
                projects["owner"]["id"]: "read_write",
                projects["outsider"]["id"]: "read",
            },
        )

        resp = wiki_put(
            ctx["url"],
            projects["owner"]["id"],
            "born-multi",
            "Born Multi",
            "body",
            tags=[SHARE_TAG, "second-contract"],
        )
        assert resp.status_code == 409
        assert sorted(e["tag"] for e in resp.json()["publishes"]) == sorted(
            [SHARE_TAG, "second-contract"]
        )
        assert (
            wiki_get(ctx["url"], projects["owner"]["id"], "born-multi").status_code
            == 404
        )

        resp = wiki_put(
            ctx["url"],
            projects["owner"]["id"],
            "born-multi",
            "Born Multi",
            "body",
            tags=[SHARE_TAG, "second-contract"],
            acknowledge_share=True,
        )
        assert resp.status_code == 201
        assert sorted(resp.json()["tags"]) == sorted([SHARE_TAG, "second-contract"])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_page_carrying_share_tag_requires_acknowledgment(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)

        resp = wiki_put(
            ctx["url"],
            projects["owner"]["id"],
            "born-shared",
            "Born Shared",
            "body",
            tags=[SHARE_TAG],
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "share_tag_requires_confirmation"
        assert (
            wiki_get(ctx["url"], projects["owner"]["id"], "born-shared").status_code
            == 404
        )

        resp = wiki_put(
            ctx["url"],
            projects["owner"]["id"],
            "born-shared",
            "Born Shared",
            "body",
            tags=[SHARE_TAG],
            acknowledge_share=True,
        )
        assert resp.status_code == 201
        assert resp.json()["tags"] == [SHARE_TAG]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_plain_tag_needs_no_acknowledgment(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)

        resp = wiki_put(
            ctx["url"],
            projects["owner"]["id"],
            "plain",
            "Plain",
            "body",
            tags=["ordinary"],
        )
        assert resp.status_code == 201
        assert resp.json()["tags"] == ["ordinary"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_revoked_member_loses_access_immediately(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "revocable", tagged=True)

        before = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "revocable",
            session_id=sessions["read_member"],
        )
        assert before.status_code == 200

        resp = httpx.delete(
            f"{ctx['url']}/api/v1/wiki/tags/{tag_id}/members/"
            f"{projects['read_member']['id']}",
            timeout=5,
        )
        assert resp.status_code == 204

        after = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "revocable",
            session_id=sessions["read_member"],
        )
        assert after.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_untagging_one_of_two_share_tags_keeps_page_shared(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        make_share_tag(
            ctx["url"],
            "second-tag",
            {
                projects["owner"]["id"]: "read_write",
                projects["read_member"]["id"]: "read",
            },
        )

        assert (
            wiki_put(
                ctx["url"],
                projects["owner"]["id"],
                "double-tagged",
                "Doc",
                "body",
                tags=[SHARE_TAG, "second-tag"],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        assert (
            wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "double-tagged",
                1,
                remove_tags=[SHARE_TAG],
            ).status_code
            == 200
        )

        resp = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "double-tagged",
            session_id=sessions["read_member"],
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_most_permissive_share_tag_wins(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        make_share_tag(
            ctx["url"],
            "writable-tag",
            {
                projects["owner"]["id"]: "read_write",
                projects["read_member"]["id"]: "read_write",
            },
        )

        assert (
            wiki_put(
                ctx["url"],
                projects["owner"]["id"],
                "union",
                "Doc",
                "body text",
                tags=[SHARE_TAG, "writable-tag"],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        resp = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "union",
            session_id=sessions["read_member"],
        )
        assert resp.status_code == 200
        assert resp.json()["access"] == "read_write"

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "union",
            1,
            edits=[{"old_string": "body", "new_string": "edited"}],
            session_id=sessions["read_member"],
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_slug_addressed_request_authorizes_identically_to_uuid(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "canonical", tagged=True)
        materialize_page(ctx["url"], projects["owner"]["id"], "hidden", tagged=False)
        owner_uuid = projects["owner"]["id"]
        owner_slug = projects["owner"]["slug"]

        for session, slug, expected in [
            (sessions["read_member"], "canonical", 200),
            (sessions["read_member"], "hidden", 404),
            (sessions["outsider"], "canonical", 404),
        ]:
            by_uuid = wiki_get(ctx["url"], owner_uuid, slug, session_id=session)
            by_slug = wiki_get(ctx["url"], owner_slug, slug, session_id=session)
            assert by_uuid.status_code == expected
            assert by_slug.status_code == expected, (
                f"slug addressing diverged for {slug}"
            )

        for address in [owner_uuid, owner_slug]:
            resp = httpx.get(
                f"{ctx['url']}/api/v1/projects/{address}/wiki/pages",
                headers=bearer(sessions["read_member"]),
                timeout=5,
            )
            assert resp.status_code == 403
            assert resp.json()["error"] == ERR_CROSS_PROJECT_COLLECTION


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cross_project_member_gets_the_same_occ_conflict_as_the_owner(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "contended", tagged=True)

        assert (
            wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "contended",
                2,
                edits=[{"old_string": "matrix body", "new_string": "owner body"}],
            ).status_code
            == 200
        )

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "contended",
            2,
            edits=[{"old_string": "matrix body", "new_string": "member body"}],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "revision_conflict"
        assert data["current_page"]["revision_number"] == 3
        assert data["current_page"]["body"] == "owner body text"
        assert data["current_page"]["project_id"] == projects["owner"]["id"]

        resp = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "contended",
            3,
            edits=[{"old_string": "owner body", "new_string": "member body"}],
            session_id=sessions["write_member"],
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_every_wiki_route_accepts_a_project_slug(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "addressed", tagged=False)
        uuid_base = f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
        slug_base = f"{ctx['url']}/api/v1/projects/{projects['owner']['slug']}"
        headers = bearer(sessions["owner"])

        paths = [
            ("/wiki/pages", {}),
            ("/wiki/pages/addressed", {}),
            ("/wiki/pages/addressed/revisions", {}),
            ("/wiki/pages/addressed/revisions/1", {}),
            ("/wiki/changes", {}),
            ("/wiki/tags", {}),
            ("/wiki/export", {}),
            ("/wiki/subscriptions", {"subscriber": "owner-agent"}),
        ]
        for path, params in paths:
            by_uuid = httpx.get(
                f"{uuid_base}{path}", params=params, headers=headers, timeout=5
            )
            by_slug = httpx.get(
                f"{slug_base}{path}", params=params, headers=headers, timeout=5
            )
            assert by_uuid.status_code == 200, path
            assert by_slug.status_code == 200, path
            assert by_slug.json() == by_uuid.json(), path

        assert (
            wiki_put(
                ctx["url"],
                projects["owner"]["slug"],
                "by-slug",
                "By Slug",
                "body",
                session_id=sessions["owner"],
            ).status_code
            == 201
        )
        assert (
            wiki_patch(
                ctx["url"],
                projects["owner"]["slug"],
                "by-slug",
                1,
                edits=[{"old_string": "body", "new_string": "edited"}],
                session_id=sessions["owner"],
            ).status_code
            == 200
        )
        assert (
            httpx.delete(
                f"{slug_base}/wiki/pages/by-slug", headers=headers, timeout=5
            ).status_code
            == 204
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_own_project_addressed_by_slug_is_not_treated_as_foreign(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "mine", tagged=False)

        resp = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['slug']}/wiki/pages",
            headers=bearer(sessions["owner"]),
            timeout=5,
        )
        assert resp.status_code == 200
        assert [p["slug"] for p in resp.json()] == ["mine"]


BAD_BODIES = [
    (
        "invalid_json",
        {"content": b"{not json", "headers": {"content-type": "application/json"}},
    ),
    ("unknown_field", {"json": {"totally_invented": 1}}),
]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_auth_answer_wins_over_body_validation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)
        materialize_page(
            ctx["url"], projects["owner"]["id"], "unreachable", tagged=False
        )
        foreign = projects["owner"]["id"]
        outsider = sessions["outsider"]

        routes = [
            (
                "put foreign page",
                "PUT",
                f"/api/v1/projects/{foreign}/wiki/pages/anything",
                (403, "cross_project_create"),
            ),
            (
                "patch unreachable page",
                "PATCH",
                f"/api/v1/projects/{foreign}/wiki/pages/unreachable",
                (400, None),
            ),
            (
                "post foreign subscription",
                "POST",
                f"/api/v1/projects/{foreign}/wiki/subscriptions",
                (403, "cross_project_collection"),
            ),
            (
                "admit member",
                "POST",
                f"/api/v1/wiki/tags/{tag_id}/members",
                (403, "operator_scope_only"),
            ),
            (
                "set member access",
                "PATCH",
                f"/api/v1/wiki/tags/{tag_id}/members/{projects['read_member']['id']}",
                (403, "operator_scope_only"),
            ),
        ]

        tokens = [
            ("malformed", {"Authorization": "NotBearer abc"}, 401),
            ("unknown", {"Authorization": f"Bearer {uuid.uuid4()}"}, 401),
        ]

        for label, method, path, (agent_status, agent_error) in routes:
            for body_label, body_kwargs in BAD_BODIES:
                for token_label, token_headers, expected in tokens:
                    kwargs = dict(body_kwargs)
                    kwargs["headers"] = {
                        **kwargs.get("headers", {}),
                        **token_headers,
                    }
                    resp = httpx.request(
                        method, f"{ctx['url']}{path}", timeout=5, **kwargs
                    )
                    assert resp.status_code == expected, (
                        f"{label} / {body_label} / {token_label}: "
                        f"{resp.status_code} {resp.text[:120]}"
                    )

                kwargs = dict(body_kwargs)
                kwargs["headers"] = {
                    **kwargs.get("headers", {}),
                    **bearer(outsider),
                }
                resp = httpx.request(method, f"{ctx['url']}{path}", timeout=5, **kwargs)
                assert resp.status_code == agent_status, (
                    f"{label} / {body_label} / agent_session: "
                    f"{resp.status_code} {resp.text[:120]}"
                )
                if agent_error is not None:
                    assert resp.json()["error"] == agent_error, (
                        f"{label} / {body_label}: {resp.text[:120]}"
                    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_body_answer_wins_once_authorization_has_passed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "mine", tagged=False)
        own = projects["owner"]["id"]

        for body_label, body_kwargs in BAD_BODIES:
            kwargs = dict(body_kwargs)
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                **bearer(sessions["owner"]),
            }
            created = httpx.request(
                "PUT",
                f"{ctx['url']}/api/v1/projects/{own}/wiki/pages/fresh",
                timeout=5,
                **kwargs,
            )
            assert created.status_code == 400, f"{body_label}: {created.text[:120]}"

            patched = httpx.request(
                "PATCH",
                f"{ctx['url']}/api/v1/projects/{own}/wiki/pages/mine",
                timeout=5,
                **kwargs,
            )
            assert patched.status_code == 400, f"{body_label}: {patched.text[:120]}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_taken_slug_is_answered_before_the_publication_confirmation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        assert (
            wiki_put(ctx["url"], owner, "occupied", "First", "body").status_code == 201
        )

        resp = wiki_put(
            ctx["url"], owner, "occupied", "Second", "body", tags=[SHARE_TAG]
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "slug_exists"
        assert "publishes" not in resp.json()

        page = wiki_get(ctx["url"], owner, "occupied")
        assert page.status_code == 200
        assert page.json()["title"] == "First"
        assert page.json()["tags"] == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_revision_is_answered_before_the_publication_confirmation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "moving", tagged=False)

        resp = wiki_patch(ctx["url"], owner, "moving", 1, add_tags=[SHARE_TAG])
        assert resp.status_code == 409
        assert resp.json()["error"] == "revision_conflict"
        assert "publishes" not in resp.json()

        page = wiki_get(ctx["url"], owner, "moving")
        assert page.status_code == 200
        assert page.json()["tags"] == []
        assert page.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_readding_a_carried_tag_asks_for_nothing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "already-out", tagged=True)
        before = wiki_get(ctx["url"], owner, "already-out")
        assert before.json()["tags"] == [SHARE_TAG]
        revision = before.json()["revision_number"]

        resp = wiki_patch(
            ctx["url"], owner, "already-out", revision, add_tags=[SHARE_TAG]
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["tags"] == [SHARE_TAG]
        assert resp.json()["revision_number"] == revision

        make_share_tag(
            ctx["url"],
            "second-room",
            {
                projects["owner"]["id"]: "read_write",
                projects["read_member"]["id"]: "read",
            },
        )
        asks = wiki_patch(
            ctx["url"],
            owner,
            "already-out",
            revision,
            add_tags=[SHARE_TAG, "second-room"],
        )
        assert asks.status_code == 409
        assert asks.json()["error"] == "share_tag_requires_confirmation"
        assert [e["tag"] for e in asks.json()["publishes"]] == ["second-room"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_a_session_asked_to_terminate_is_not_a_wiki_principal(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "guarded", tagged=True)
        owner = projects["owner"]["id"]
        member = sessions["write_member"]

        assert (
            wiki_get(ctx["url"], owner, "guarded", session_id=member).status_code == 200
        )

        set_session_fields(ctx["db_url"], member, desired="terminate")

        assert (
            wiki_get(ctx["url"], owner, "guarded", session_id=member).status_code == 401
        )
        write = wiki_patch(
            ctx["url"],
            owner,
            "guarded",
            2,
            edits=[{"old_string": "matrix body", "new_string": "x"}],
            session_id=member,
        )
        assert write.status_code == 401, write.text
        assert wiki_search(ctx["url"], session_id=member, q="matrix").status_code == 401

        page = wiki_get(ctx["url"], owner, "guarded")
        assert page.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_a_session_under_a_terminal_execution_is_not_a_wiki_principal(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "guarded", tagged=True)
        owner = projects["owner"]["id"]
        member = sessions["write_member"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT execution_id FROM sessions WHERE id = ?", (member,)
            ).fetchone()
        execution_id = row[0]

        for field in ({"desired": "terminate"}, {"outcome": "completed"}):
            set_execution_fields(ctx["db_url"], execution_id, **field)
            assert (
                wiki_get(ctx["url"], owner, "guarded", session_id=member).status_code
                == 401
            ), f"execution {field} did not end the session's authority"
            assert (
                wiki_search(ctx["url"], session_id=member, q="matrix").status_code
                == 401
            )
            set_execution_fields(
                ctx["db_url"], execution_id, desired="run", outcome=None
            )
