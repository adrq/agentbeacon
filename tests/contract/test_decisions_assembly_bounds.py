# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

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

SCAN_CHUNK = 500
SCAN_BUDGET = 4

DECISIONS = 60


def _walk_page(url, before=None, limit=None):
    params = {"state": "resolved"}
    if before is not None:
        params["before"] = before
    if limit is not None:
        params["limit"] = limit
    resp = httpx.get(f"{url}/api/v1/decisions", params=params, timeout=60)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _dismiss(url, event_id):
    resp = httpx.post(f"{url}/api/v1/escalate/{event_id}/dismiss", timeout=15)
    assert resp.status_code == 200, resp.text


def _pad_platform_lane(db_url, exec_id, session_id, count):
    payload = json.dumps({"parts": [{"data": {"type": "turn_complete"}}]})
    batch = 500
    with db_conn(db_url) as conn:
        for start in range(0, count, batch):
            rows = min(batch, count - start)
            values = ", ".join(["(?, ?, 'platform', ?)"] * rows)
            params = []
            for _ in range(rows):
                params.extend([exec_id, session_id, payload])
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                f"VALUES {values}",
                tuple(params),
            )
        conn.commit()


@DUAL_BACKEND
def test_a_budget_stop_returns_a_short_page_that_resumes_exactly(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="assembly-budget")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        deep = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Deep?"}]}
        )
        _dismiss(ctx["url"], deep["event_id"])

        _pad_platform_lane(
            test_database, exec_id, session_id, SCAN_CHUNK * SCAN_BUDGET + 50
        )

        first = _walk_page(ctx["url"], limit=50)
        assert first["items"] == [], "the budget stopped before reaching the decision"
        assert first["has_more"] is True
        assert first["next_cursor"] is not None

        seen = []
        cursor = first["next_cursor"]
        for _ in range(20):
            page = _walk_page(ctx["url"], before=cursor, limit=50)
            seen.extend(i["event_id"] for i in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]

        assert deep["event_id"] in seen
        assert len(seen) == len(set(seen))


@DUAL_BACKEND
def test_page_assembly_reads_executions_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="assembly-executions-only")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "task", title="Assembly Title"
        )
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Q?"}]}
        )
        _dismiss(ctx["url"], decision["event_id"])

        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE events SET session_id = NULL WHERE session_id = ?",
                (session_id,),
            )
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.execute("DELETE FROM execution_agents WHERE agent_id = ?", (agent_id,))
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            conn.commit()

        page = _walk_page(ctx["url"])
        assert isinstance(page["items"], list)

        agent2 = seed_test_agent(ctx["db_url"], name="assembly-second")
        exec2, session2 = create_execution_via_api(
            ctx["url"], agent2, "task", title="Second Title"
        )
        second = rest_escalate_ok(
            ctx["url"], session2, {"questions": [{"question": "Q2?"}]}
        )
        _dismiss(ctx["url"], second["event_id"])

        page = _walk_page(ctx["url"])
        row = next(i for i in page["items"] if i["event_id"] == second["event_id"])
        assert row["execution_id"] == exec2
        assert row["execution_title"] == "Second Title"


@DUAL_BACKEND
def test_a_page_is_bounded_regardless_of_lane_size(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="assembly-bounded")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for i in range(60):
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )
            _dismiss(ctx["url"], decision["event_id"])

        started = time.perf_counter()
        page = _walk_page(ctx["url"], limit=10)
        elapsed = time.perf_counter() - started

        assert len(page["items"]) == 10
        assert page["has_more"] is True
        assert elapsed < 5.0


def _tuples_read(url, db_url, tables):
    time.sleep(1.2)
    for _ in range(40):
        httpx.get(f"{url}/api/ready", timeout=10)
    time.sleep(1.2)
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT relname, COALESCE(seq_tup_read, 0) + COALESCE(idx_tup_fetch, 0) "
            "FROM pg_stat_user_tables WHERE relname = ANY(%s)",
            (list(tables),),
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_page_assembly_reads_no_session_or_agent_rows(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="assembly-cost")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "task", title="Cost Title"
        )
        for i in range(DECISIONS):
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )
            _dismiss(ctx["url"], decision["event_id"])

        tables = ("sessions", "agents")

        def cost(limit):
            before = _tuples_read(ctx["url"], test_database, tables)
            page = _walk_page(ctx["url"], limit=limit)
            after = _tuples_read(ctx["url"], test_database, tables)
            return page, {t: after.get(t, 0) - before.get(t, 0) for t in tables}

        _tuples_read(ctx["url"], test_database, tables)
        one_page, one = cost(1)
        many_page, many = cost(DECISIONS)
        assert len(one_page["items"]) == 1
        assert len(many_page["items"]) == DECISIONS, "the corpus must be assembled"

        for table in tables:
            growth = many[table] - one[table]
            assert growth < DECISIONS // 2, (table, growth, one, many)
