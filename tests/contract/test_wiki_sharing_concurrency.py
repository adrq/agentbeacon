# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg2
import pytest

from tests.wiki_helpers import (
    CROSS_PROJECT_WRITES_FLAG,
    SHARE_TAG,
    build_world,
    materialize_page,
    admit_member,
    bearer,
    make_share_tag,
    find_wiki_tag,
    membership_rows,
    seed_tag,
    set_config_flag,
    wiki_search,
    set_member_access,
    tag_id_for,
    wiki_get,
    wiki_patch,
    wiki_put,
)
from tests.testhelpers import (
    create_execution_via_api,
    create_project_via_api,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import db_conn, set_execution_fields, set_session_fields


BLOCKED_WAIT_TIMEOUT_SECS = 15.0
BLOCKED_POLL_INTERVAL_SECS = 0.05

ORIGINAL_BODY = "matrix body text"
EDITED_BODY = "revalidated body text"

WAITERS_SQL = """
SELECT count(*) FROM pg_stat_activity a
JOIN pg_locks l ON l.pid = a.pid
JOIN pg_class c ON c.oid = l.relation
WHERE a.datname = current_database() AND a.pid <> pg_backend_pid()
  AND a.wait_event_type = 'Lock'
  AND (l.locktype = 'tuple' OR NOT l.granted)
  AND c.relname = %s
"""


def wait_for_lock_waiter(observer_conn, relation, future):
    deadline = time.monotonic() + BLOCKED_WAIT_TIMEOUT_SECS
    while time.monotonic() < deadline:
        with observer_conn.cursor() as cur:
            cur.execute(WAITERS_SQL, (relation,))
            waiters = cur.fetchone()[0]
        observer_conn.rollback()
        if waiters:
            return
        if future.done():
            raise AssertionError(
                f"no lock waiter was observed on {relation} before the write completed "
                f"(response: {future.result()!r})"
            )
        time.sleep(BLOCKED_POLL_INTERVAL_SECS)
    raise AssertionError(
        f"no lock waiter was observed on {relation} within {BLOCKED_WAIT_TIMEOUT_SECS}s"
    )


def race_and_release(
    ctx, lock_sql, lock_params, relation, mutate_sql, mutate_params, request_fn
):
    lock_conn = psycopg2.connect(ctx["db_url"])
    observer_conn = psycopg2.connect(ctx["db_url"])
    try:
        lock_conn.autocommit = False
        with lock_conn.cursor() as cur:
            cur.execute(lock_sql, lock_params)
            assert cur.fetchone() is not None, "nothing to contend on"

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request_fn)
            wait_for_lock_waiter(observer_conn, relation, future)

            with lock_conn.cursor() as cur:
                cur.execute(mutate_sql, mutate_params)
            lock_conn.commit()
            return future.result(timeout=15)
    finally:
        observer_conn.close()
        lock_conn.close()


def assert_page_untouched(ctx, project_id, slug):
    page = wiki_get(ctx["url"], project_id, slug)
    assert page.status_code == 200
    assert page.json()["body"] == ORIGINAL_BODY
    assert page.json()["revision_number"] == 2


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_membership_revalidated_inside_write_transaction(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "raced", tagged=True)

        resp = race_and_release(
            ctx,
            "SELECT m.id FROM wiki_tag_members m JOIN wiki_tags t ON t.id = m.tag_id "
            "WHERE t.name = %s AND m.project_id = %s AND m.revoked_at IS NULL "
            "FOR UPDATE OF m",
            (SHARE_TAG, projects["write_member"]["id"]),
            "wiki_tag_members",
            "UPDATE wiki_tag_members SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE tag_id = (SELECT id FROM wiki_tags WHERE name = %s) "
            "AND project_id = %s AND revoked_at IS NULL",
            (SHARE_TAG, projects["write_member"]["id"]),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "raced",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 404, resp.text
        assert_page_untouched(ctx, projects["owner"]["id"], "raced")


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_untag_concurrent_with_cross_project_write_denies_the_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(
            ctx["url"], projects["owner"]["id"], "untagged-race", tagged=True
        )

        resp = race_and_release(
            ctx,
            "SELECT pt.page_id FROM wiki_page_tags pt "
            "JOIN wiki_pages p ON p.id = pt.page_id "
            "JOIN wiki_tags t ON t.id = pt.tag_id "
            "WHERE p.slug = %s AND p.project_id = %s AND t.name = %s "
            "FOR UPDATE OF pt",
            ("untagged-race", projects["owner"]["id"], SHARE_TAG),
            "wiki_page_tags",
            "DELETE FROM wiki_page_tags WHERE tag_id = "
            "(SELECT id FROM wiki_tags WHERE name = %s) AND page_id = "
            "(SELECT id FROM wiki_pages WHERE slug = %s AND project_id = %s)",
            (SHARE_TAG, "untagged-race", projects["owner"]["id"]),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "untagged-race",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 404, resp.text
        assert_page_untouched(ctx, projects["owner"]["id"], "untagged-race")


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_access_downgrade_concurrent_with_cross_project_write_denies_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "downgraded", tagged=True)

        resp = race_and_release(
            ctx,
            "SELECT m.id FROM wiki_tag_members m JOIN wiki_tags t ON t.id = m.tag_id "
            "WHERE t.name = %s AND m.project_id = %s AND m.revoked_at IS NULL "
            "FOR UPDATE OF m",
            (SHARE_TAG, projects["write_member"]["id"]),
            "wiki_tag_members",
            "UPDATE wiki_tag_members SET access_level = 'read' "
            "WHERE tag_id = (SELECT id FROM wiki_tags WHERE name = %s) "
            "AND project_id = %s AND revoked_at IS NULL",
            (SHARE_TAG, projects["write_member"]["id"]),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "downgraded",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "read_only_member"
        assert_page_untouched(ctx, projects["owner"]["id"], "downgraded")


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_owner_row_race_serialises_the_foreign_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "page-lock", tagged=True)

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_pages WHERE slug = %s AND project_id = %s "
            "FOR UPDATE OF wiki_pages",
            ("page-lock", projects["owner"]["id"]),
            "wiki_pages",
            "UPDATE wiki_pages SET revision_number = revision_number + 1, "
            "body = 'owner rewrote it' WHERE slug = %s AND project_id = %s",
            ("page-lock", projects["owner"]["id"]),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "page-lock",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "revision_conflict"
        assert resp.json()["current_page"]["revision_number"] == 3

        page = wiki_get(ctx["url"], projects["owner"]["id"], "page-lock")
        assert page.json()["body"] == "owner rewrote it"
        assert page.json()["revision_number"] == 3


def count_waiters(observer_conn, relation):
    with observer_conn.cursor() as cur:
        cur.execute(WAITERS_SQL, (relation,))
        n = cur.fetchone()[0]
    observer_conn.rollback()
    return n


BLOCKED_BACKENDS_SQL = """
SELECT DISTINCT a.pid FROM pg_stat_activity a
WHERE a.datname = current_database() AND a.pid <> pg_backend_pid()
  AND a.wait_event_type = 'Lock'
"""


def blocked_pids(observer_conn) -> set:
    with observer_conn.cursor() as cur:
        cur.execute(BLOCKED_BACKENDS_SQL)
        pids = {row[0] for row in cur.fetchall()}
    observer_conn.rollback()
    return pids


def wait_for_a_newly_blocked_backend(observer_conn, baseline, future):
    deadline = time.monotonic() + BLOCKED_WAIT_TIMEOUT_SECS
    while time.monotonic() < deadline:
        if blocked_pids(observer_conn) - baseline and not future.done():
            return
        if future.done():
            raise AssertionError(
                f"the request finished without ever blocking "
                f"(response: {future.result()!r})"
            )
        time.sleep(BLOCKED_POLL_INTERVAL_SECS)
    raise AssertionError("no backend blocked beyond those already waiting")


def wait_for_both_newly_blocked(observer_conn, baseline, futures):
    deadline = time.monotonic() + BLOCKED_WAIT_TIMEOUT_SECS
    newly = set()
    while time.monotonic() < deadline:
        finished = [f for f in futures if f.done()]
        if finished:
            raise AssertionError(
                f"a request finished while both project rows were still held "
                f"(responses: {[f.result() for f in finished]!r})"
            )
        newly = blocked_pids(observer_conn) - baseline
        if len(newly) >= 2:
            return newly
        time.sleep(BLOCKED_POLL_INTERVAL_SECS)
    raise AssertionError(
        f"only {len(newly)} backend(s) blocked beyond the baseline within "
        f"{BLOCKED_WAIT_TIMEOUT_SECS}s, expected 2"
    )


def wait_for_waiter_on_either(observer_conn, relations, future):
    deadline = time.monotonic() + BLOCKED_WAIT_TIMEOUT_SECS
    while time.monotonic() < deadline:
        if any(count_waiters(observer_conn, r) for r in relations):
            return
        if future.done():
            raise AssertionError(
                f"the request finished without waiting on any of {relations} "
                f"(response: {future.result()!r})"
            )
        time.sleep(BLOCKED_POLL_INTERVAL_SECS)
    raise AssertionError(f"no backend waited on any of {relations}")


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_owner_untag_patch_serialises_with_a_foreign_edit(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "contended", tagged=True)

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM wiki_pages WHERE slug = %s AND project_id = %s "
                    "FOR UPDATE OF wiki_pages",
                    ("contended", projects["owner"]["id"]),
                )
                assert cur.fetchone() is not None

            with ThreadPoolExecutor(max_workers=2) as executor:
                owner = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    projects["owner"]["id"],
                    "contended",
                    2,
                    remove_tags=[SHARE_TAG],
                )
                wait_for_waiter_on_either(
                    observer_conn, ["wiki_pages", "wiki_page_tags"], owner
                )
                owner_parked = blocked_pids(observer_conn)

                foreign = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    projects["owner"]["id"],
                    "contended",
                    2,
                    edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                    session_id=sessions["write_member"],
                )
                wait_for_a_newly_blocked_backend(observer_conn, owner_parked, foreign)

                lock_conn.rollback()
                owner_resp = owner.result(timeout=30)
                foreign_resp = foreign.result(timeout=30)
        finally:
            observer_conn.close()
            lock_conn.close()

        assert owner_resp.status_code == 200, f"owner: {owner_resp.text}"
        assert foreign_resp.status_code == 404, f"foreign: {foreign_resp.text}"

        page = wiki_get(ctx["url"], projects["owner"]["id"], "contended")
        assert page.status_code == 200
        assert page.json()["revision_number"] == 3
        assert page.json()["tags"] == []
        assert page.json()["body"] == ORIGINAL_BODY


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_caller_project_soft_delete_concurrent_with_a_foreign_write_denies_it(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(
            ctx["url"], projects["owner"]["id"], "caller-gone", tagged=True
        )

        resp = race_and_release(
            ctx,
            "SELECT id FROM projects WHERE id = %s AND deleted_at IS NULL "
            "FOR UPDATE OF projects",
            (projects["write_member"]["id"],),
            "projects",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (projects["write_member"]["id"],),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "caller-gone",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 404, resp.text
        assert_page_untouched(ctx, projects["owner"]["id"], "caller-gone")


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_owner_project_soft_delete_concurrent_with_a_foreign_write_denies_it(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "owner-gone", tagged=True)

        resp = race_and_release(
            ctx,
            "SELECT id FROM projects WHERE id = %s AND deleted_at IS NULL "
            "FOR UPDATE OF projects",
            (projects["owner"]["id"],),
            "projects",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (projects["owner"]["id"],),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "owner-gone",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 404, resp.text


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_reciprocal_cross_project_writes_do_not_deadlock(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "owner-page", tagged=True)
        materialize_page(
            ctx["url"], projects["write_member"]["id"], "member-page", tagged=True
        )

        ordered = sorted([projects["owner"]["id"], projects["write_member"]["id"]])
        lock_a = psycopg2.connect(ctx["db_url"])
        lock_b = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            for conn, project_id in ((lock_a, ordered[0]), (lock_b, ordered[1])):
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM projects WHERE id = %s FOR UPDATE OF projects",
                        (project_id,),
                    )
                    assert cur.fetchone() is not None

            baseline = blocked_pids(observer_conn)

            with ThreadPoolExecutor(max_workers=2) as executor:
                forward = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    projects["owner"]["id"],
                    "owner-page",
                    2,
                    edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                    session_id=sessions["write_member"],
                )
                reverse = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    projects["write_member"]["id"],
                    "member-page",
                    2,
                    edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                    session_id=sessions["owner"],
                )

                wait_for_both_newly_blocked(observer_conn, baseline, [forward, reverse])

                lock_a.rollback()
                lock_b.rollback()
                forward_resp = forward.result(timeout=30)
                reverse_resp = reverse.result(timeout=30)
        finally:
            observer_conn.close()
            lock_a.close()
            lock_b.close()

        for label, resp in [("forward", forward_resp), ("reverse", reverse_resp)]:
            assert resp.status_code in (200, 403, 404, 409), (
                f"{label} got {resp.status_code}: {resp.text}"
            )
        assert forward_resp.status_code == 200, forward_resp.text
        assert reverse_resp.status_code == 200, reverse_resp.text


def admission_race(ctx, owner_project, joiner_project, tag_id, request_fn):
    lock_conn = psycopg2.connect(ctx["db_url"])
    observer_conn = psycopg2.connect(ctx["db_url"])
    try:
        lock_conn.autocommit = False
        with lock_conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM projects WHERE id = %s FOR UPDATE OF projects",
                (owner_project,),
            )
            assert cur.fetchone() is not None, "nothing to contend on"

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(request_fn)
            wait_for_lock_waiter(observer_conn, "projects", future)

            admission = executor.submit(
                admit_member, ctx["url"], tag_id, joiner_project, "read"
            )
            wait_for_lock_waiter(observer_conn, "wiki_tags", admission)

            lock_conn.rollback()
            return future.result(timeout=30), admission.result(timeout=30)
    finally:
        observer_conn.close()
        lock_conn.close()


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_create_cannot_publish_through_a_concurrent_admission(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="pub-race-agent")
        owner = create_project_via_api(ctx["url"], "pub-race-owner")
        joiner = create_project_via_api(ctx["url"], "pub-race-joiner")
        create_execution_via_api(ctx["url"], agent, "x", project_id=owner["id"])

        seed_tag(ctx["url"], "solo")
        tag_id = tag_id_for(ctx["url"], "solo")
        assert (
            admit_member(ctx["url"], tag_id, owner["id"], "read_write").status_code
            == 201
        )

        resp, admitted = admission_race(
            ctx,
            owner["id"],
            joiner["id"],
            tag_id,
            lambda: wiki_put(
                ctx["url"], owner["id"], "raced-in", "Raced In", "body", tags=["solo"]
            ),
        )

        assert resp.status_code == 201, resp.text

        assert admitted.status_code == 409, admitted.text
        assert admitted.json()["error"] == "membership_requires_confirmation"
        assert admitted.json()["exposes_to_joiner"] == [
            {
                "project": owner["slug"],
                "page_count": 1,
                "pages": [{"slug": "raced-in", "title": "Raced In"}],
            }
        ]

        entry = find_wiki_tag(ctx["url"], "solo")
        assert joiner["id"] not in [m["project_id"] for m in entry["members"]]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_upgrade_holds_the_tag_row_across_its_disclosure_and_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "already", tagged=True)

        latecomer = create_project_via_api(ctx["url"], "matrix-latecomer")
        assert (
            admit_member(
                ctx["url"],
                tag_id,
                latecomer["id"],
                "read_write",
                acknowledge_share=True,
            ).status_code
            == 201
        )
        materialize_page(ctx["url"], latecomer["id"], "late-page", tagged=True)
        with psycopg2.connect(ctx["db_url"]) as scrub, scrub.cursor() as cur:
            cur.execute(
                "UPDATE wiki_tag_members SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE tag_id = %s AND project_id = %s",
                (tag_id, latecomer["id"]),
            )

        member = projects["read_member"]["id"]
        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE id = %s FOR UPDATE OF wiki_tags",
            (tag_id,),
            "wiki_tags",
            "UPDATE wiki_tag_members SET revoked_at = NULL "
            "WHERE tag_id = %s AND project_id = %s",
            (tag_id, latecomer["id"]),
            lambda: set_member_access(ctx["url"], tag_id, member, "read_write"),
        )

        assert resp.status_code == 409, resp.text
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        disclosed = {entry["project"]: entry for entry in data["grants_write"]}
        assert latecomer["slug"] in disclosed, data["grants_write"]
        assert disclosed[latecomer["slug"]]["pages"] == [
            {"slug": "late-page", "title": "Matrix Page"}
        ]
        assert disclosed[projects["owner"]["slug"]]["pages"] == [
            {"slug": "already", "title": "Matrix Page"}
        ]

        entry = find_wiki_tag(ctx["url"], SHARE_TAG)
        levels = {m["project_id"]: m["access_level"] for m in entry["members"]}
        assert levels[member] == "read"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_duplicate_admission_under_the_lock_is_a_conflict_not_a_crash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        joiner = projects["outsider"]["id"]

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE id = %s FOR UPDATE OF wiki_tags",
            (tag_id,),
            "wiki_tags",
            "INSERT INTO wiki_tag_members (id, tag_id, project_id, access_level) "
            "VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), tag_id, joiner, "read"),
            lambda: admit_member(
                ctx["url"], tag_id, joiner, "read_write", acknowledge_share=True
            ),
        )

        assert resp.status_code == 409, f"{resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["error"] == "member_exists"
        assert data["access_level"] == "read"
        assert "PATCH" in data["remedy"]

        entry = find_wiki_tag(ctx["url"], SHARE_TAG)
        rows = [m for m in entry["members"] if m["project_id"] == joiner]
        assert len(rows) == 1
        assert rows[0]["access_level"] == "read"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_joiner_deleted_mid_admission_commits_no_membership(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        joiner = projects["outsider"]["id"]

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE id = %s FOR UPDATE OF wiki_tags",
            (tag_id,),
            "wiki_tags",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (joiner,),
            lambda: admit_member(
                ctx["url"], tag_id, joiner, "read", acknowledge_share=True
            ),
        )

        assert membership_rows(ctx["db_url"], SHARE_TAG, joiner) == []

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"] == "project_not_found"

        entry = find_wiki_tag(ctx["url"], SHARE_TAG)
        assert joiner not in [m["project_id"] for m in entry["members"]]
        assert len(entry["members"]) == 3


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_member_deleted_mid_upgrade_answers_as_the_serial_path_does(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        member = projects["read_member"]["id"]
        materialize_page(
            ctx["url"], projects["owner"]["id"], "upgradeable", tagged=True
        )

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE id = %s FOR UPDATE OF wiki_tags",
            (tag_id,),
            "wiki_tags",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (member,),
            lambda: set_member_access(
                ctx["url"], tag_id, member, "read_write", acknowledge_share=True
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"] == "project_not_found"

        rows = membership_rows(ctx["db_url"], SHARE_TAG, member)
        assert len(rows) == 1
        assert rows[0][1] == "read"
        assert rows[0][2] is None


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_revoke_landing_inside_the_upgrade_answers_member_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        member = projects["read_member"]["id"]

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tag_members WHERE tag_id = %s AND project_id = %s "
            "AND revoked_at IS NULL FOR UPDATE OF wiki_tag_members",
            (tag_id, member),
            "wiki_tag_members",
            "UPDATE wiki_tag_members SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE tag_id = %s AND project_id = %s AND revoked_at IS NULL",
            (tag_id, member),
            lambda: set_member_access(
                ctx["url"], tag_id, member, "read_write", acknowledge_share=True
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"] == "member_not_found"

        rows = membership_rows(ctx["db_url"], SHARE_TAG, member)
        assert len(rows) == 1
        assert rows[0][1] == "read"
        assert rows[0][2] is not None


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_kill_switch_flipped_mid_write_stops_the_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "switched", tagged=True)

        assert (
            set_config_flag(ctx["url"], CROSS_PROJECT_WRITES_FLAG, True).status_code
            == 200
        )

        resp = race_and_release(
            ctx,
            "SELECT p.id FROM wiki_pages p JOIN projects pr ON pr.id = p.project_id "
            "WHERE pr.id = %s AND p.slug = %s AND p.deleted_at IS NULL "
            "FOR UPDATE OF p",
            (projects["owner"]["id"], "switched"),
            "wiki_pages",
            "UPDATE config SET value = 'false' WHERE name = %s",
            (CROSS_PROJECT_WRITES_FLAG,),
            lambda: wiki_patch(
                ctx["url"],
                projects["owner"]["id"],
                "switched",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"] == "cross_project_writes_disabled"

        assert_page_untouched(ctx, projects["owner"]["id"], "switched")
        revisions = httpx.get(
            f"{ctx['url']}/api/v1/projects/{projects['owner']['id']}"
            f"/wiki/pages/switched/revisions",
            timeout=5,
        )
        assert revisions.status_code == 200
        assert [r["revision_number"] for r in revisions.json()] == [1]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_reported_access_reflects_a_flip_that_lands_before_the_read_snapshot(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        materialize_page(ctx["url"], projects["owner"]["id"], "reported", tagged=True)
        assert (
            set_config_flag(ctx["url"], CROSS_PROJECT_WRITES_FLAG, True).status_code
            == 200
        )

        settled = wiki_get(
            ctx["url"],
            projects["owner"]["id"],
            "reported",
            session_id=sessions["write_member"],
        )
        assert settled.status_code == 200
        assert settled.json()["access"] == "read_write"

        hold_config = psycopg2.connect(ctx["db_url"])
        hold_projects = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            hold_config.autocommit = False
            hold_projects.autocommit = False
            with hold_config.cursor() as cur:
                cur.execute("LOCK TABLE config IN ACCESS EXCLUSIVE MODE")

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    wiki_get,
                    ctx["url"],
                    projects["owner"]["id"],
                    "reported",
                    session_id=sessions["write_member"],
                )
                wait_for_lock_waiter(observer_conn, "config", future)

                with hold_projects.cursor() as cur:
                    cur.execute("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE")

                hold_config.rollback()

                wait_for_lock_waiter(observer_conn, "projects", future)

                with hold_projects.cursor() as cur:
                    cur.execute(
                        "UPDATE config SET value = 'false' WHERE name = %s",
                        (CROSS_PROJECT_WRITES_FLAG,),
                    )
                hold_projects.commit()
                resp = future.result(timeout=15)
        finally:
            observer_conn.close()
            hold_config.close()
            hold_projects.close()

        assert resp.status_code == 200, resp.text
        assert resp.json()["access"] == "read"

        refused = wiki_patch(
            ctx["url"],
            projects["owner"]["id"],
            "reported",
            2,
            edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
            session_id=sessions["write_member"],
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "cross_project_writes_disabled"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_stale_after_the_handler_read_is_a_revision_conflict(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "overtaken", tagged=False)

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE name = %s FOR UPDATE OF wiki_tags",
            (SHARE_TAG,),
            "wiki_tags",
            "UPDATE wiki_pages SET revision_number = revision_number + 1 "
            "WHERE project_id = %s AND slug = %s",
            (owner, "overtaken"),
            lambda: wiki_patch(ctx["url"], owner, "overtaken", 2, add_tags=[SHARE_TAG]),
        )

        assert resp.status_code == 409, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"] == "revision_conflict"
        assert "publishes" not in resp.json()

        page = wiki_get(ctx["url"], owner, "overtaken")
        assert page.status_code == 200
        assert page.json()["tags"] == []


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_tag_arriving_mid_flight_is_a_revision_conflict(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "overtaken-tag", tagged=False)

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM wiki_tags WHERE name = %s FOR UPDATE OF wiki_tags",
                    (SHARE_TAG,),
                )
                assert cur.fetchone() is not None

            def add_tag():
                return wiki_patch(
                    ctx["url"],
                    owner,
                    "overtaken-tag",
                    2,
                    add_tags=[SHARE_TAG],
                    acknowledge_share=True,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(add_tag)
                wait_for_lock_waiter(observer_conn, "wiki_tags", first)

                baseline = blocked_pids(observer_conn)
                second = executor.submit(add_tag)
                wait_for_a_newly_blocked_backend(observer_conn, baseline, second)

                lock_conn.rollback()
                results = [first.result(timeout=30), second.result(timeout=30)]
        finally:
            observer_conn.close()
            lock_conn.close()

        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 409], [(r.status_code, r.text) for r in results]
        stale = next(r for r in results if r.status_code == 409)
        assert stale.json()["error"] == "revision_conflict", stale.text

        page = wiki_get(ctx["url"], owner, "overtaken-tag")
        assert page.status_code == 200
        assert page.json()["tags"] == [SHARE_TAG]
        assert page.json()["revision_number"] == 3


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_owner_create_parked_when_its_project_is_deleted_writes_nothing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE name = %s FOR UPDATE OF wiki_tags",
            (SHARE_TAG,),
            "wiki_tags",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (owner,),
            lambda: wiki_put(
                ctx["url"], owner, "orphan", "Orphan", "body", tags=[SHARE_TAG]
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

        with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM wiki_pages WHERE project_id = %s AND slug = %s",
                (owner, "orphan"),
            )
            assert cur.fetchone()[0] == 0, "a page was written for a deleted project"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_owner_patch_parked_when_its_project_is_deleted_writes_nothing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "orphaned", tagged=False)

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE name = %s FOR UPDATE OF wiki_tags",
            (SHARE_TAG,),
            "wiki_tags",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (owner,),
            lambda: wiki_patch(
                ctx["url"],
                owner,
                "orphaned",
                2,
                add_tags=[SHARE_TAG],
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

        with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT p.revision_number, p.body, "
                "  (SELECT count(*) FROM wiki_page_revisions r WHERE r.page_id = p.id) "
                "FROM wiki_pages p WHERE p.project_id = %s AND p.slug = %s",
                (owner, "orphaned"),
            )
            revision, body, archived = cur.fetchone()
        assert revision == 2
        assert body == ORIGINAL_BODY
        assert archived == 1


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_admission_cannot_overtake_a_parked_publication(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent = seed_test_agent(ctx["db_url"], name="overtake-agent")
        owner = create_project_via_api(ctx["url"], "overtake-owner")
        joiner = create_project_via_api(ctx["url"], "overtake-joiner")
        create_execution_via_api(ctx["url"], agent, "x", project_id=owner["id"])

        seed_tag(ctx["url"], "solo-overtake")
        tag_id = tag_id_for(ctx["url"], "solo-overtake")
        assert (
            admit_member(ctx["url"], tag_id, owner["id"], "read_write").status_code
            == 201
        )
        assert (
            wiki_put(ctx["url"], owner["id"], "quiet", "Quiet", "body").status_code
            == 201
        )

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM wiki_pages WHERE project_id = %s AND slug = %s "
                    "FOR UPDATE OF wiki_pages",
                    (owner["id"], "quiet"),
                )
                assert cur.fetchone() is not None

            with ThreadPoolExecutor(max_workers=2) as executor:
                patch = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    owner["id"],
                    "quiet",
                    1,
                    add_tags=["solo-overtake"],
                )
                wait_for_waiter_on_either(observer_conn, ["wiki_pages"], patch)

                admission = executor.submit(
                    admit_member, ctx["url"], tag_id, joiner["id"], "read"
                )
                wait_for_lock_waiter(observer_conn, "wiki_tags", admission)

                lock_conn.rollback()
                patch_resp = patch.result(timeout=30)
                admission_resp = admission.result(timeout=30)
        finally:
            observer_conn.close()
            lock_conn.close()

        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["tags"] == ["solo-overtake"]

        assert admission_resp.status_code == 409, admission_resp.text
        assert admission_resp.json()["error"] == "membership_requires_confirmation"
        assert admission_resp.json()["exposes_to_joiner"] == [
            {
                "project": owner["slug"],
                "page_count": 1,
                "pages": [{"slug": "quiet", "title": "Quiet"}],
            }
        ]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_writes_to_different_pages_in_one_project_do_not_serialise(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "page-one", tagged=False)
        materialize_page(ctx["url"], owner, "page-two", tagged=False)

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM wiki_pages WHERE project_id = %s AND slug = %s "
                    "FOR UPDATE OF wiki_pages",
                    (owner, "page-one"),
                )
                assert cur.fetchone() is not None

            with ThreadPoolExecutor(max_workers=2) as executor:
                blocked = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    owner,
                    "page-one",
                    2,
                    edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                )
                wait_for_lock_waiter(observer_conn, "wiki_pages", blocked)

                independent = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    owner,
                    "page-two",
                    2,
                    edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                )
                independent_resp = independent.result(timeout=15)
                assert independent_resp.status_code == 200, independent_resp.text
                assert not blocked.done(), "the contended write should still be parked"
                assert count_waiters(observer_conn, "projects") == 0

                lock_conn.rollback()
                assert blocked.result(timeout=30).status_code == 200
        finally:
            observer_conn.close()
            lock_conn.close()


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_slug_reuse_cannot_substitute_a_page_under_an_authorized_write(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "contract", tagged=True)

        impostor_id = str(uuid.uuid4())
        resp = race_and_release(
            ctx,
            "SELECT m.id FROM wiki_tag_members m JOIN wiki_tags t ON t.id = m.tag_id "
            "WHERE t.name = %s AND m.project_id = %s AND m.revoked_at IS NULL "
            "FOR UPDATE OF m",
            (SHARE_TAG, projects["write_member"]["id"]),
            "wiki_tag_members",
            "UPDATE wiki_pages SET deleted_at = CURRENT_TIMESTAMP "
            "  WHERE project_id = %s AND slug = %s AND deleted_at IS NULL; "
            "INSERT INTO wiki_pages (id, project_id, slug, title, body, revision_number) "
            "  VALUES (%s, %s, %s, 'Impostor', %s, 2)",
            (owner, "contract", impostor_id, owner, "contract", ORIGINAL_BODY),
            lambda: wiki_patch(
                ctx["url"],
                owner,
                "contract",
                2,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["write_member"],
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

        with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT body, revision_number, title FROM wiki_pages WHERE id = %s",
                (impostor_id,),
            )
            body, revision, title = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM wiki_page_revisions WHERE page_id = %s",
                (impostor_id,),
            )
            archived = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM wiki_page_tags WHERE page_id = %s", (impostor_id,)
            )
            tags = cur.fetchone()[0]

        assert body == ORIGINAL_BODY
        assert revision == 2
        assert title == "Impostor"
        assert archived == 0
        assert tags == 0


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_overlapping_tag_writers_do_not_queue_behind_each_other(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "page-one", tagged=False)
        materialize_page(ctx["url"], owner, "page-two", tagged=False)
        seed_tag(ctx["url"], "z-existing")
        solo = tag_id_for(ctx["url"], "z-existing")
        assert admit_member(ctx["url"], solo, owner, "read_write").status_code == 201

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM wiki_pages WHERE project_id = %s AND slug = %s "
                    "FOR UPDATE OF wiki_pages",
                    (owner, "page-one"),
                )
                assert cur.fetchone() is not None

            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    owner,
                    "page-one",
                    2,
                    add_tags=["z-existing"],
                )
                wait_for_lock_waiter(observer_conn, "wiki_pages", holder)

                other = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    owner,
                    "page-two",
                    2,
                    add_tags=["a-brand-new", "z-existing"],
                    acknowledge_share=True,
                )

                try:
                    other_resp = other.result(timeout=BLOCKED_WAIT_TIMEOUT_SECS)
                except Exception as e:
                    raise AssertionError(
                        f"the second writer never got through while the first was "
                        f"parked ({e!r})"
                    ) from e
                assert other_resp.status_code == 200, other_resp.text
                assert not holder.done(), "the first writer was released early"
                assert count_waiters(observer_conn, "wiki_tags") == 0

                lock_conn.rollback()
                holder_resp = holder.result(timeout=30)
        finally:
            observer_conn.close()
            lock_conn.close()

        for label, resp in [("holder", holder_resp), ("other", other_resp)]:
            assert "deadlock" not in resp.text.lower(), f"{label}: {resp.text}"
            assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text}"

        assert wiki_get(ctx["url"], owner, "page-one").json()["tags"] == ["z-existing"]
        assert sorted(wiki_get(ctx["url"], owner, "page-two").json()["tags"]) == [
            "a-brand-new",
            "z-existing",
        ]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_tag_that_does_not_exist_yet_still_participates_in_publication(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        joiner = projects["outsider"]["id"]
        materialize_page(ctx["url"], owner, "quiet", tagged=False)
        materialize_page(ctx["url"], owner, "quiet-two", tagged=False)

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM wiki_pages WHERE project_id = %s AND slug = %s "
                    "FOR UPDATE OF wiki_pages",
                    (owner, "quiet"),
                )
                assert cur.fetchone() is not None

            with ThreadPoolExecutor(max_workers=2) as executor:
                writer = executor.submit(
                    wiki_patch,
                    ctx["url"],
                    owner,
                    "quiet",
                    2,
                    add_tags=["not-yet-existing"],
                    acknowledge_share=True,
                )
                wait_for_lock_waiter(observer_conn, "wiki_pages", writer)

                def create_same_tag():
                    return wiki_patch(
                        ctx["url"],
                        owner,
                        "quiet-two",
                        2,
                        add_tags=["not-yet-existing"],
                        acknowledge_share=True,
                    )

                baseline = blocked_pids(observer_conn)
                contender = executor.submit(create_same_tag)
                wait_for_a_newly_blocked_backend(observer_conn, baseline, contender)

                assert find_wiki_tag(ctx["url"], "not-yet-existing") is None

                lock_conn.rollback()
                assert writer.result(timeout=30).status_code == 200
                assert contender.result(timeout=30).status_code == 200
        finally:
            observer_conn.close()
            lock_conn.close()

        tag_id = tag_id_for(ctx["url"], "not-yet-existing")

        assert admit_member(ctx["url"], tag_id, owner, "read_write").status_code == 201

        refused = admit_member(ctx["url"], tag_id, joiner, "read")
        assert refused.status_code == 409, refused.text
        data = refused.json()
        assert data["error"] == "membership_requires_confirmation"
        exposed = {e["project"]: e for e in data["exposes_to_joiner"]}
        owner_slug = projects["owner"]["slug"]
        assert owner_slug in exposed, data["exposes_to_joiner"]
        assert sorted(p["slug"] for p in exposed[owner_slug]["pages"]) == [
            "quiet",
            "quiet-two",
        ]

        entry = find_wiki_tag(ctx["url"], "not-yet-existing")
        assert joiner not in [m["project_id"] for m in entry["members"]]

        with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM wiki_tags WHERE name = 'not-yet-existing'"
            )
            assert cur.fetchone()[0] == 1
        for slug in ("quiet", "quiet-two"):
            assert wiki_get(ctx["url"], owner, slug).json()["tags"] == [
                "not-yet-existing"
            ]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_confirmation_labels_come_from_post_lock_state(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, tag_id = build_world(ctx)
        owner = projects["owner"]
        materialize_page(ctx["url"], owner["id"], "exposed", tagged=True)
        joiner = create_project_via_api(ctx["url"], "label-joiner")
        reclaimer = create_project_via_api(ctx["url"], "label-reclaimer")

        original = owner["slug"]
        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE id = %s FOR UPDATE OF wiki_tags",
            (tag_id,),
            "wiki_tags",
            "UPDATE projects SET slug = 'renamed-owner' WHERE id = %s; "
            "UPDATE projects SET slug = %s WHERE id = %s",
            (owner["id"], original, reclaimer["id"]),
            lambda: admit_member(ctx["url"], tag_id, joiner["id"], "read"),
        )

        assert resp.status_code == 409, f"{resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"

        exposed = {e["project"]: e for e in data["exposes_to_joiner"]}
        assert "renamed-owner" in exposed, data["exposes_to_joiner"]
        assert exposed["renamed-owner"]["pages"] == [
            {"slug": "exposed", "title": "Matrix Page"}
        ]
        assert original not in exposed, (
            f"labelled with a slug that now belongs to another project: {data}"
        )


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_subscription_create_parked_when_its_project_is_deleted_writes_nothing(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        projects, _, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "watched", tagged=False)

        resp = race_and_release(
            ctx,
            "SELECT id FROM projects WHERE id = %s FOR UPDATE OF projects",
            (owner,),
            "projects",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (owner,),
            lambda: httpx.post(
                f"{ctx['url']}/api/v1/projects/{owner}/wiki/subscriptions",
                json={"subscriber": "watcher", "page_slug": "watched"},
                timeout=5,
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

        with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM wiki_subscriptions WHERE project_id = %s",
                (owner,),
            )
            assert cur.fetchone()[0] == 0, (
                "a subscription was written for a deleted project"
            )


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_session_terminated_mid_write_commits_nothing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        session_id = sessions["owner"]
        with db_conn(ctx["db_url"]) as conn:
            execution_id = conn.execute(
                "SELECT execution_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]

        def revive():
            set_session_fields(ctx["db_url"], session_id, desired="run", outcome=None)
            set_execution_fields(
                ctx["db_url"], execution_id, desired="run", outcome=None
            )

        def rows(sql, params):
            with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

        def case_patch(tag):
            materialize_page(ctx["url"], owner, f"patched-{tag}", tagged=False)
            return (
                "SELECT id FROM wiki_pages WHERE project_id = %s AND slug = %s "
                "FOR UPDATE OF wiki_pages",
                (owner, f"patched-{tag}"),
                "wiki_pages",
                lambda: wiki_patch(
                    ctx["url"],
                    owner,
                    f"patched-{tag}",
                    2,
                    edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                    session_id=session_id,
                ),
                lambda: rows(
                    "SELECT revision_number, body FROM wiki_pages "
                    "WHERE project_id = %s AND slug = %s",
                    (owner, f"patched-{tag}"),
                )
                == [(2, ORIGINAL_BODY)],
            )

        def case_delete(tag):
            materialize_page(ctx["url"], owner, f"deleted-{tag}", tagged=False)
            return (
                "SELECT id FROM wiki_pages WHERE project_id = %s AND slug = %s "
                "FOR UPDATE OF wiki_pages",
                (owner, f"deleted-{tag}"),
                "wiki_pages",
                lambda: httpx.delete(
                    f"{ctx['url']}/api/v1/projects/{owner}/wiki/pages/deleted-{tag}",
                    headers=bearer(session_id),
                    timeout=5,
                ),
                lambda: rows(
                    "SELECT deleted_at FROM wiki_pages "
                    "WHERE project_id = %s AND slug = %s",
                    (owner, f"deleted-{tag}"),
                )
                == [(None,)],
            )

        def case_create(tag):
            return (
                "SELECT id FROM projects WHERE id = %s FOR UPDATE OF projects",
                (owner,),
                "projects",
                lambda: wiki_put(
                    ctx["url"],
                    owner,
                    f"created-{tag}",
                    "Created",
                    "body",
                    session_id=session_id,
                ),
                lambda: rows(
                    "SELECT count(*) FROM wiki_pages WHERE project_id = %s AND slug = %s",
                    (owner, f"created-{tag}"),
                )
                == [(0,)],
            )

        def case_subscribe(tag):
            return (
                "SELECT id FROM projects WHERE id = %s FOR UPDATE OF projects",
                (owner,),
                "projects",
                lambda: httpx.post(
                    f"{ctx['url']}/api/v1/projects/{owner}/wiki/subscriptions",
                    json={"subscriber": f"watcher-{tag}", "tag_name": "anything"},
                    headers=bearer(session_id),
                    timeout=5,
                ),
                lambda: rows(
                    "SELECT count(*) FROM wiki_subscriptions "
                    "WHERE project_id = %s AND subscriber = %s",
                    (owner, f"watcher-{tag}"),
                )
                == [(0,)],
            )

        def case_unsubscribe(tag):
            created = httpx.post(
                f"{ctx['url']}/api/v1/projects/{owner}/wiki/subscriptions",
                json={"subscriber": f"watcher2-{tag}", "tag_name": "something"},
                headers=bearer(session_id),
                timeout=5,
            )
            assert created.status_code in (200, 201), created.text
            sub_id = created.json()["id"]
            return (
                "SELECT id FROM wiki_subscriptions WHERE id = %s "
                "FOR UPDATE OF wiki_subscriptions",
                (sub_id,),
                "wiki_subscriptions",
                lambda: httpx.delete(
                    f"{ctx['url']}/api/v1/projects/{owner}/wiki/subscriptions/{sub_id}",
                    headers=bearer(session_id),
                    timeout=5,
                ),
                lambda: rows(
                    "SELECT count(*) FROM wiki_subscriptions WHERE id = %s", (sub_id,)
                )
                == [(1,)],
            )

        cases = [
            ("patch", case_patch),
            ("delete", case_delete),
            ("create", case_create),
            ("subscribe", case_subscribe),
            ("unsubscribe", case_unsubscribe),
        ]
        kills = [
            ("session", "UPDATE sessions SET desired = 'terminate' WHERE id = %s"),
            ("execution", "UPDATE executions SET desired = 'terminate' WHERE id = %s"),
        ]

        for case_name, build in cases:
            for kill_name, kill_sql in kills:
                revive()
                lock_sql, lock_params, relation, request, untouched = build(kill_name)
                target = session_id if kill_name == "session" else execution_id
                resp = race_and_release(
                    ctx, lock_sql, lock_params, relation, kill_sql, (target,), request
                )
                label = f"{case_name}/{kill_name}"
                assert resp.status_code == 401, (
                    f"{label}: {resp.status_code} {resp.text}"
                )
                assert untouched(), f"{label}: the database was mutated"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_page_delete_parked_when_its_project_is_deleted_writes_nothing(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "doomed", tagged=True)

        resp = race_and_release(
            ctx,
            "SELECT id FROM projects WHERE id = %s FOR UPDATE OF projects",
            (owner,),
            "projects",
            "UPDATE projects SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (owner,),
            lambda: httpx.delete(
                f"{ctx['url']}/api/v1/projects/{owner}/wiki/pages/doomed",
                headers=bearer(sessions["owner"]),
                timeout=5,
            ),
        )

        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

        with psycopg2.connect(ctx["db_url"]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT deleted_at FROM wiki_pages WHERE project_id = %s AND slug = %s",
                (owner, "doomed"),
            )
            assert cur.fetchone()[0] is None, (
                "the page was deleted after its project was"
            )


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_alternate_tag_reachability_is_one_observation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        owner = create_project_via_api(ctx["url"], "reach-owner")
        joiner = create_project_via_api(ctx["url"], "reach-joiner")

        alt = make_share_tag(
            ctx["url"],
            "alt-route",
            {owner["id"]: "read_write", joiner["id"]: "read"},
        )
        target = make_share_tag(ctx["url"], "target-tag", {owner["id"]: "read_write"})

        assert (
            wiki_put(
                ctx["url"],
                owner["id"],
                "x-page",
                "X Page",
                "body",
                tags=["target-tag"],
                acknowledge_share=True,
            ).status_code
            == 201
        )

        lock_conn = psycopg2.connect(ctx["db_url"])
        observer_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute("LOCK TABLE wiki_pages IN ACCESS EXCLUSIVE MODE")

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    admit_member, ctx["url"], target, joiner["id"], "read"
                )
                wait_for_lock_waiter(observer_conn, "wiki_pages", future)

                with lock_conn.cursor() as cur:
                    cur.execute(
                        "UPDATE wiki_tag_members SET revoked_at = CURRENT_TIMESTAMP "
                        "WHERE tag_id = %s AND project_id = %s",
                        (alt, joiner["id"]),
                    )
                    cur.execute(
                        "INSERT INTO wiki_page_tags (page_id, tag_id) "
                        "SELECT p.id, %s FROM wiki_pages p "
                        "WHERE p.project_id = %s AND p.slug = 'x-page' "
                        "ON CONFLICT DO NOTHING",
                        (alt, owner["id"]),
                    )
                lock_conn.commit()
                resp = future.result(timeout=30)
        finally:
            observer_conn.close()
            lock_conn.close()

        assert resp.status_code == 409, f"{resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["error"] == "membership_requires_confirmation"
        exposed = {e["project"]: e for e in data["exposes_to_joiner"]}
        assert owner["slug"] in exposed, data["exposes_to_joiner"]
        assert exposed[owner["slug"]]["pages"] == [
            {"slug": "x-page", "title": "X Page"}
        ]

        entry = find_wiki_tag(ctx["url"], "target-tag")
        assert joiner["id"] not in [m["project_id"] for m in entry["members"]]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_read_cannot_use_authority_its_principal_never_held(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, tag_id = build_world(ctx)
        owner = projects["owner"]["id"]
        outsider_project = projects["outsider"]["id"]
        outsider = sessions["outsider"]
        materialize_page(ctx["url"], owner, "secret", tagged=True)

        with db_conn(ctx["db_url"]) as conn:
            execution_id = conn.execute(
                "SELECT execution_id FROM sessions WHERE id = ?", (outsider,)
            ).fetchone()[0]

        assert (
            wiki_get(ctx["url"], owner, "secret", session_id=outsider).status_code
            == 404
        )

        reads = [
            (
                "page",
                lambda: wiki_get(ctx["url"], owner, "secret", session_id=outsider),
            ),
            (
                "collection",
                lambda: httpx.get(
                    f"{ctx['url']}/api/v1/projects/{outsider_project}/wiki/pages",
                    headers=bearer(outsider),
                    timeout=5,
                ),
            ),
            (
                "search",
                lambda: wiki_search(ctx["url"], session_id=outsider, q="matrix"),
            ),
        ]

        for label, request in reads:
            set_session_fields(ctx["db_url"], outsider, desired="run", outcome=None)
            set_execution_fields(
                ctx["db_url"], execution_id, desired="run", outcome=None
            )
            with db_conn(ctx["db_url"]) as conn:
                conn.execute(
                    "DELETE FROM wiki_tag_members WHERE tag_id = "
                    "(SELECT id FROM wiki_tags WHERE name = ?) AND project_id = ?",
                    (SHARE_TAG, outsider_project),
                )
                conn.commit()

            lock_conn = psycopg2.connect(ctx["db_url"])
            observer_conn = psycopg2.connect(ctx["db_url"])
            try:
                lock_conn.autocommit = False
                with lock_conn.cursor() as cur:
                    cur.execute("LOCK TABLE wiki_tag_members IN ACCESS EXCLUSIVE MODE")

                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(request)
                    wait_for_lock_waiter(observer_conn, "wiki_tag_members", future)

                    with lock_conn.cursor() as cur:
                        cur.execute(
                            "UPDATE sessions SET desired = 'terminate' WHERE id = %s",
                            (outsider,),
                        )
                        cur.execute(
                            "INSERT INTO wiki_tag_members "
                            "(id, tag_id, project_id, access_level) "
                            "VALUES (gen_random_uuid()::text, %s, %s, 'read')",
                            (tag_id, outsider_project),
                        )
                    lock_conn.commit()
                    resp = future.result(timeout=30)
            finally:
                observer_conn.close()
                lock_conn.close()

            assert resp.status_code == 401, f"{label}: {resp.status_code} {resp.text}"
            body = resp.text
            assert "secret" not in body, f"{label} leaked the page: {body[:200]}"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_write_names_the_project_as_it_held_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]
        thief = create_project_via_api(ctx["url"], "slug-thief")
        original = owner["slug"]
        materialize_page(ctx["url"], owner["id"], "renamed-under", tagged=False)

        resp = race_and_release(
            ctx,
            "SELECT id FROM wiki_tags WHERE name = %s FOR UPDATE OF wiki_tags",
            (SHARE_TAG,),
            "wiki_tags",
            "UPDATE projects SET slug = 'renamed-owner' WHERE id = %s; "
            "UPDATE projects SET slug = %s WHERE id = %s",
            (owner["id"], original, thief["id"]),
            lambda: wiki_patch(
                ctx["url"],
                original,
                "renamed-under",
                2,
                add_tags=[SHARE_TAG],
                acknowledge_share=True,
                edits=[{"old_string": ORIGINAL_BODY, "new_string": EDITED_BODY}],
                session_id=sessions["owner"],
            ),
        )

        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["project_id"] == owner["id"]
        assert body["project_slug"] == "renamed-owner", body
        assert body["project_slug"] != original
        assert body["revision_number"] == 3
