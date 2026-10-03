# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import concurrent.futures
import json
import time

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
    start_worker,
)
from tests.mock_agent_helpers import (
    create_child_session_raw,
    make_executor_report,
    make_turn_result,
    rest_escalate_ok,
    seed_acp_mock_agent,
    set_session_fields,
    post_worker_sync,
)


def _tail(url, exec_id, after=None, limit=500):
    params = {"limit": limit}
    if after is not None:
        params["after"] = after
    resp = httpx.get(
        f"{url}/api/v1/executions/{exec_id}/events", params=params, timeout=15
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _platform_kinds(db_url, exec_id, session_id=None):
    sql = (
        "SELECT payload FROM events WHERE execution_id = ? AND event_type = 'platform'"
    )
    params = [exec_id]
    if session_id is not None:
        sql += " AND session_id = ?"
        params.append(session_id)
    with db_conn(db_url) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    kinds = set()
    for (payload,) in rows:
        body = json.loads(payload)
        if body.get("type"):
            kinds.add(body["type"])
        for part in body.get("parts", []):
            kind = (part.get("data") or {}).get("type")
            if kind:
                kinds.add(kind)
    return kinds


def _assert_ordered_and_tailable(db_url, url, exec_id):
    committed = _all_ids(db_url, exec_id)
    assert committed == sorted(committed)
    listed = [int(e["id"]) for e in _tail(url, exec_id)["items"]]
    assert listed == committed
    return committed


def _all_ids(db_url, exec_id):
    with db_conn(db_url) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT id FROM events WHERE execution_id = ? ORDER BY id ASC",
                (exec_id,),
            ).fetchall()
        ]


@DUAL_BACKEND
def test_concurrent_writers_never_strand_a_lower_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="id-order")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "race")

        stop = False
        seen = []
        missed = []

        def tail():
            watermark = 0
            while not stop:
                page = _tail(
                    ctx["url"], exec_id, after=watermark if watermark else None
                )
                for item in page["items"]:
                    current = int(item["id"])
                    if current <= watermark:
                        missed.append(current)
                    watermark = max(watermark, current)
                    seen.append(current)
                time.sleep(0.001)

        with concurrent.futures.ThreadPoolExecutor(max_workers=17) as pool:
            reader = pool.submit(tail)
            writers = [
                pool.submit(
                    rest_escalate_ok,
                    ctx["url"],
                    session_id,
                    {"questions": [{"question": f"Q{i}?"}]},
                )
                for i in range(80)
            ]
            for w in writers:
                w.result()

            committed = _all_ids(test_database, exec_id)
            for _ in range(40):
                time.sleep(0.25)
                current = _all_ids(test_database, exec_id)
                if current == committed:
                    break
                committed = current
            time.sleep(1.0)
            stop = True
            reader.result()

        assert missed == [], f"ids served at or below the reader's cursor: {missed}"
        assert _all_ids(test_database, exec_id) == committed, (
            "the committed ids changed after the snapshot was taken"
        )

        assert set(seen) == set(committed), (
            f"committed ids never served: {sorted(set(committed) - set(seen))}; "
            f"served ids never committed: {sorted(set(seen) - set(committed))}"
        )


@DUAL_BACKEND
def test_escalate_writes_are_served_in_order(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="id-order-escalate")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = [
                r.result()
                for r in [
                    pool.submit(
                        rest_escalate_ok,
                        ctx["url"],
                        session_id,
                        {"questions": [{"question": f"Q{i}?"}]},
                    )
                    for i in range(16)
                ]
            ]

        written = sorted(int(r["event_id"]) for r in results)
        assert len(set(written)) == 16
        listed = [int(e["id"]) for e in _tail(ctx["url"], exec_id)["items"]]
        assert set(written) <= set(listed)
        assert listed == sorted(listed)


@DUAL_BACKEND
def test_execution_creation_writes_are_served_in_order(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="id-order-create")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        ids = _all_ids(test_database, exec_id)
        assert len(ids) >= 2
        assert ids == sorted(ids)
        listed = [int(e["id"]) for e in _tail(ctx["url"], exec_id)["items"]]
        assert listed == ids


@DUAL_BACKEND
def test_worker_event_writes_are_served_in_order(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )
        worker = start_worker(ctx["url"], interval="500ms")
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                status = httpx.get(
                    f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5
                ).json()["execution"]["status"]
                if status in ("completed", "failed", "awaiting_input"):
                    break
                time.sleep(0.5)
            time.sleep(1.0)

            with db_conn(test_database) as conn:
                types = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT event_type FROM events WHERE execution_id = ?",
                        (exec_id,),
                    ).fetchall()
                }
            assert {"message", "state_change", "platform"} <= types, types
            assert "turn_complete" in _platform_kinds(
                test_database, exec_id, session_id
            )

            committed = _assert_ordered_and_tailable(test_database, ctx["url"], exec_id)
            assert len(committed) > 2
        finally:
            cleanup_processes([worker])


@DUAL_BACKEND
def test_parent_notification_writes_are_served_in_order(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="id-order-delegate")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) "
                "VALUES (?, ?)",
                (exec_id, agent_id),
            )
            conn.commit()

        lead = post_worker_sync(ctx["url"], worker_id="w-lead")
        assert lead["action"]["session_id"] == lead_id
        post_worker_sync(
            ctx["url"],
            "w-lead",
            executor_report=make_executor_report(lead_id, "running"),
            command_ack=lead["token"],
        )

        child_id = json.loads(
            mcp_tools_call(
                ctx["url"],
                lead_id,
                "delegate",
                {"agent": "id-order-delegate", "prompt": "child task"},
            )["content"][0]["text"]
        )["session_id"]

        child = post_worker_sync(ctx["url"], worker_id="w-child")
        assert child["action"]["session_id"] == child_id
        post_worker_sync(
            ctx["url"],
            "w-child",
            executor_report=make_executor_report(child_id, "running"),
            command_ack=child["token"],
        )
        post_worker_sync(
            ctx["url"],
            "w-child",
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[{"role": "ROLE_AGENT", "parts": [{"text": "child done"}]}],
            ),
        )

        assert "turn_complete" in _platform_kinds(test_database, exec_id, lead_id), (
            "the parent notification was not written"
        )
        _assert_ordered_and_tailable(test_database, ctx["url"], exec_id)


@DUAL_BACKEND
def test_reconciler_notice_writes_are_served_in_order(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent = seed_test_agent(ctx["db_url"], name="id-order-lead")
        child_agent = seed_test_agent(ctx["db_url"], name="id-order-child")
        exec_id, parent_id = create_execution_via_api(
            ctx["url"], lead_agent, "coordinate"
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) "
                "VALUES (?, ?)",
                (exec_id, child_agent),
            )
            conn.commit()

        child_id = create_child_session_raw(
            test_database,
            parent_id,
            exec_id,
            child_agent,
            desired="run",
            executor_state="crashed",
        )
        set_session_fields(
            test_database,
            parent_id,
            desired="run",
            executor_state="idle",
            agent_session_id="parent-sdk-1",
            worker_id="w-parent",
        )
        with db_conn(test_database) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (parent_id,))
            conn.commit()
        set_session_fields(
            test_database,
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            if "child_crashed" in _platform_kinds(test_database, exec_id, parent_id):
                break
            time.sleep(0.25)

        assert "child_crashed" in _platform_kinds(test_database, exec_id, parent_id), (
            "the parent's crash notice was not written"
        )
        _assert_ordered_and_tailable(test_database, ctx["url"], exec_id)
