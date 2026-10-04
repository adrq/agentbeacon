# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok


def _walk_page(url, before=None, limit=None):
    params = {"state": "resolved"}
    if before is not None:
        params["before"] = before
    if limit is not None:
        params["limit"] = limit
    resp = httpx.get(f"{url}/api/v1/decisions", params=params, timeout=30)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _walk_all(url, limit=None):
    seen = []
    cursor = None
    for _ in range(200):
        page = _walk_page(url, before=cursor, limit=limit)
        seen.extend(page["items"])
        if not page["has_more"]:
            return seen, page
        assert page["next_cursor"] is not None
        cursor = page["next_cursor"]
    raise AssertionError("the walk did not terminate")


def _dismiss(url, event_id):
    resp = httpx.post(f"{url}/api/v1/escalate/{event_id}/dismiss", timeout=15)
    assert resp.status_code == 200, resp.text


@DUAL_BACKEND
def test_page_one_is_inclusive_of_the_newest_platform_row(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-page-one")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Only?"}]}
        )
        _dismiss(ctx["url"], decision["event_id"])

        with db_conn(test_database) as conn:
            newest = conn.execute(
                "SELECT MAX(id) FROM events WHERE event_type = 'platform'"
            ).fetchone()[0]
        assert newest is not None

        page = _walk_page(ctx["url"])
        assert [i["event_id"] for i in page["items"]] == [decision["event_id"]]


@DUAL_BACKEND
def test_a_full_walk_emits_each_decision_exactly_once(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-exactly-once")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        created = []
        for i in range(60):
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )
            _dismiss(ctx["url"], decision["event_id"])
            created.append(decision["event_id"])

        seen, last = _walk_all(ctx["url"], limit=7)
        ids = [i["event_id"] for i in seen]
        assert len(ids) == len(set(ids)), "no decision is emitted twice"
        assert set(created) <= set(ids)
        assert last["next_cursor"] is None

        positions = [ids.index(e) for e in created]
        assert positions == sorted(positions, reverse=True)


@DUAL_BACKEND
def test_cursors_strictly_decrease(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-cursors")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for i in range(30):
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )
            _dismiss(ctx["url"], decision["event_id"])

        cursors = []
        cursor = None
        for _ in range(50):
            page = _walk_page(ctx["url"], before=cursor, limit=4)
            if not page["has_more"]:
                break
            cursors.append(int(page["next_cursor"]))
            cursor = page["next_cursor"]

        assert cursors == sorted(cursors, reverse=True)
        assert len(cursors) == len(set(cursors))


@DUAL_BACKEND
def test_pending_decisions_never_appear_in_the_walk(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-pending-filter")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        resolved = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Resolved?"}]}
        )
        _dismiss(ctx["url"], resolved["event_id"])
        pending = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Pending?"}]}
        )

        seen, _ = _walk_all(ctx["url"])
        ids = {i["event_id"] for i in seen}
        assert resolved["event_id"] in ids
        assert pending["event_id"] not in ids


@DUAL_BACKEND
def test_a_mid_walk_resolution_appears_at_its_creation_position(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-mid-resolution")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Target?"}]}
        )
        others = []
        for i in range(6):
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Other{i}?"}]}
            )
            _dismiss(ctx["url"], decision["event_id"])
            others.append(decision["event_id"])

        first = _walk_page(ctx["url"], limit=2)
        assert target["event_id"] not in [i["event_id"] for i in first["items"]]

        _dismiss(ctx["url"], target["event_id"])

        seen = list(first["items"])
        cursor = first["next_cursor"]
        while cursor is not None:
            page = _walk_page(ctx["url"], before=cursor, limit=2)
            seen.extend(page["items"])
            cursor = page["next_cursor"] if page["has_more"] else None

        ids = [i["event_id"] for i in seen]
        assert target["event_id"] in ids, "a mid-walk resolution appears when reached"
        assert len(ids) == len(set(ids))


@DUAL_BACKEND
def test_a_resolution_after_its_position_appears_in_the_next_walk(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-late-resolution")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        target = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Late?"}]}
        )
        for i in range(4):
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Filler{i}?"}]}
            )
            _dismiss(ctx["url"], decision["event_id"])

        seen, _ = _walk_all(ctx["url"], limit=2)
        assert target["event_id"] not in {i["event_id"] for i in seen}

        _dismiss(ctx["url"], target["event_id"])

        seen_again, _ = _walk_all(ctx["url"], limit=2)
        assert target["event_id"] in {i["event_id"] for i in seen_again}


@DUAL_BACKEND
def test_summaries_carry_history_shape_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-summary-shape")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "task", title="Walk Title"
        )
        decision = rest_escalate_ok(
            ctx["url"],
            session_id,
            {"questions": [{"question": "First?"}, {"question": "Second?"}]},
        )
        _dismiss(ctx["url"], decision["event_id"])

        page = _walk_page(ctx["url"])
        summary = next(
            i for i in page["items"] if i["event_id"] == decision["event_id"]
        )
        assert set(summary) == {
            "event_id",
            "batch_id",
            "execution_id",
            "execution_title",
            "session_id",
            "status",
            "importance",
            "question_preview",
            "question_count",
            "created_at",
        }
        assert summary["status"] == "resolved"
        assert summary["question_preview"] == "First?"
        assert summary["question_count"] == 2
        assert summary["execution_id"] == exec_id
        assert summary["execution_title"] == "Walk Title"
