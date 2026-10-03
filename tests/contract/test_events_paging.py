# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _page(url, exec_id, **params):
    resp = httpx.get(
        f"{url}/api/v1/executions/{exec_id}/events", params=params, timeout=10
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed(ctx, count, agent_name="paging"):
    agent_id = seed_test_agent(ctx["db_url"], name=agent_name)
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "paging")
    for i in range(count):
        rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
        )
    return exec_id, session_id


@DUAL_BACKEND
def test_page_envelope_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 2)
        page = _page(ctx["url"], exec_id)
        assert set(page) == {"items", "next_cursor", "has_more"}
        assert page["has_more"] is False
        assert page["next_cursor"] is None
        for item in page["items"]:
            assert isinstance(item["id"], str)


@DUAL_BACKEND
def test_both_modes_return_ascending_pages(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 6)
        everything = [int(e["id"]) for e in _page(ctx["url"], exec_id)["items"]]
        assert everything == sorted(everything)

        cursor = everything[4]
        before = _page(ctx["url"], exec_id, before=str(cursor), limit=3)
        ids = [int(e["id"]) for e in before["items"]]
        assert ids == sorted(ids)
        assert ids == everything[1:4], "the window is the 3 rows below the cursor"
        assert int(before["items"][-1]["id"]) == max(ids), "last item is the newest"

        after = _page(ctx["url"], exec_id, after=str(cursor), limit=3)
        after_ids = [int(e["id"]) for e in after["items"]]
        assert after_ids == sorted(after_ids)
        assert after_ids == everything[5:8]


@DUAL_BACKEND
def test_paging_backwards_covers_everything_exactly_once(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 8)
        expected = [e["id"] for e in _page(ctx["url"], exec_id)["items"]]

        seen = []
        cursor = None
        for _ in range(20):
            params = {"limit": 3}
            if cursor is not None:
                params["before"] = cursor
            page = _page(ctx["url"], exec_id, **params)
            seen = [e["id"] for e in page["items"]] + seen
            if not page["has_more"]:
                break
            assert page["next_cursor"] is not None
            cursor = page["next_cursor"]

        assert seen == expected


@DUAL_BACKEND
def test_an_exact_limit_final_page_reports_no_more(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 3, agent_name="paging-boundary")
        everything = [e["id"] for e in _page(ctx["url"], exec_id)["items"]]
        total = len(everything)

        page = _page(ctx["url"], exec_id, limit=total)
        assert [e["id"] for e in page["items"]] == everything
        assert page["has_more"] is False
        assert page["next_cursor"] is None

        oldest = int(everything[0])
        after = _page(ctx["url"], exec_id, after=str(oldest), limit=total - 1)
        assert [e["id"] for e in after["items"]] == everything[1:]
        assert after["has_more"] is False
        assert after["next_cursor"] is None

        assert _page(ctx["url"], exec_id, limit=total - 1)["has_more"] is True
        assert (
            _page(ctx["url"], exec_id, after=str(oldest), limit=total - 2)["has_more"]
            is True
        )


@DUAL_BACKEND
def test_limit_is_enforced(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 3)
        assert len(_page(ctx["url"], exec_id, limit=1)["items"]) == 1

        for limit in (0, -1, 501):
            resp = httpx.get(
                f"{ctx['url']}/api/v1/executions/{exec_id}/events",
                params={"limit": limit},
                timeout=5,
            )
            assert resp.status_code == 400, limit
            assert resp.json()["code"] == "request.invalid"


@DUAL_BACKEND
def test_both_cursors_are_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 2)
        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events",
            params={"before": "5", "after": "1"},
            timeout=5,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "request.invalid"


@DUAL_BACKEND
def test_malformed_cursor_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, _ = _seed(ctx, 1)
        for cursor in ("not-a-number", "0", "-4"):
            resp = httpx.get(
                f"{ctx['url']}/api/v1/executions/{exec_id}/events",
                params={"before": cursor},
                timeout=5,
            )
            assert resp.status_code == 400, cursor
            assert resp.json()["code"] == "request.invalid"


@DUAL_BACKEND
def test_scope_isolation(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_a, session_a = _seed(ctx, 3)
        exec_b, session_b = _seed(ctx, 3, agent_name="paging-b")

        a_ids = {e["id"] for e in _page(ctx["url"], exec_a)["items"]}
        b_ids = {e["id"] for e in _page(ctx["url"], exec_b)["items"]}
        assert a_ids and b_ids
        assert a_ids.isdisjoint(b_ids)
        for item in _page(ctx["url"], exec_a)["items"]:
            assert item["execution_id"] == exec_a

        resp = httpx.get(f"{ctx['url']}/api/v1/sessions/{session_a}/events", timeout=10)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["session_id"] == session_a

        foreign = max(int(i) for i in b_ids)
        page = _page(ctx["url"], exec_a, before=str(foreign))
        assert {e["id"] for e in page["items"]} <= a_ids


@DUAL_BACKEND
def test_session_pages_share_the_envelope(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        _exec_id, session_id = _seed(ctx, 4)
        resp = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events",
            params={"limit": 2},
            timeout=10,
        )
        assert resp.status_code == 200
        page = resp.json()
        assert set(page) == {"items", "next_cursor", "has_more"}
        assert len(page["items"]) == 2
        assert page["has_more"] is True
        ids = [int(e["id"]) for e in page["items"]]
        assert ids == sorted(ids)


def _insert_corrupt(db_url, exec_id, session_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'message', ?)",
            (exec_id, session_id, b"\x80"),
        )
        conn.commit()
        return conn.execute("SELECT MAX(id) FROM events").fetchone()[0]


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_a_corrupt_row_inside_an_exact_limit_window_does_not_hide_older_rows(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="corrupt-window")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        readable = [
            int(
                rest_escalate_ok(
                    ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
                )["event_id"]
            )
            for i in range(2)
        ]
        _insert_corrupt(test_database, exec_id, session_id)
        readable += [
            int(
                rest_escalate_ok(
                    ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
                )["event_id"]
            )
            for i in range(2, 4)
        ]

        resp = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events",
            params={"limit": 2},
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert page["has_more"] is True, (
            "expected has_more to stay true past the corrupt row"
        )
        assert page["next_cursor"] is not None

        seen = [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
        for _ in range(10):
            older = httpx.get(
                f"{ctx['url']}/api/v1/sessions/{session_id}/events",
                params={"limit": 2, "before": cursor},
                timeout=10,
            ).json()
            seen = [e["id"] for e in older["items"]] + seen
            if not older["has_more"]:
                break
            assert older["next_cursor"] != cursor, "the cursor did not advance"
            cursor = older["next_cursor"]

        assert len(seen) == len(set(seen)), "no row is served twice"
        for event_id in readable:
            assert str(event_id) in seen, (
                f"row {event_id} was hidden by the corrupt row"
            )
