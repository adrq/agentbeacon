# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import concurrent.futures
import json
import time
import uuid

import httpx
import psycopg2
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate, rest_escalate_ok

NOISE_ROWS = 5_000

SQL_PROBE = (
    "SELECT e.id, e.outcome, e.desired, e.title, "
    "(SELECT MAX(ev.id) FROM events ev WHERE ev.execution_id = e.id "
    "AND ev.event_type = 'platform') AS platform_max "
    "FROM executions e WHERE e.outcome IS NULL AND e.desired != 'terminate' "
    "ORDER BY e.id ASC"
)


def _pending(url, etag=None, **params):
    headers = {"If-None-Match": etag} if etag else {}
    return httpx.get(
        f"{url}/api/v1/decisions", params=params, headers=headers, timeout=60
    )


def _seed_noise(db_url, exec_id, session_id, count):
    payload = json.dumps(
        {"parts": [{"data": {"type": "turn_complete", "child_session_id": "x"}}]}
    )
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
        if db_url.startswith("postgres"):
            conn.execute("ANALYZE events")
            conn.commit()


@DUAL_BACKEND
def test_pending_response_carries_a_validator_and_no_cache_policy(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="pending-headers")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "Q?"}]})

        resp = _pending(ctx["url"])
        assert resp.status_code == 200
        assert resp.headers["etag"].startswith('"')
        assert resp.headers["cache-control"] == "no-store"

        again = _pending(ctx["url"], etag=resp.headers["etag"])
        assert again.status_code == 304
        assert again.headers["etag"] == resp.headers["etag"]
        assert again.headers["cache-control"] == "no-store"
        assert again.content == b""


def _events_tuples_read(url, db_url):
    time.sleep(1.2)
    for _ in range(40):
        httpx.get(f"{url}/api/ready", timeout=10)
    time.sleep(1.2)
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT COALESCE(seq_tup_read, 0) + COALESCE(idx_tup_fetch, 0) "
            "FROM pg_stat_user_tables WHERE relname = 'events'"
        ).fetchone()
    return int(row[0]) if row else 0


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_an_unchanged_snapshot_costs_no_reduction(test_database):
    batch = 10

    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="pending-cost")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "Q?"}]})
        _seed_noise(test_database, exec_id, session_id, NOISE_ROWS)

        first = _pending(ctx["url"])
        assert first.status_code == 200
        etag = first.headers["etag"]
        base = _events_tuples_read(ctx["url"], test_database)

        for _ in range(batch):
            assert _pending(ctx["url"], etag=etag).status_code == 304
        after_cached = _events_tuples_read(ctx["url"], test_database)
        cached_rows = after_cached - base

        for _ in range(batch):
            assert _pending(ctx["url"]).status_code == 200
        after_full = _events_tuples_read(ctx["url"], test_database)
        full_rows = after_full - after_cached

        assert full_rows >= batch * NOISE_ROWS // 2, (
            f"{batch} reductions should have read the lane, saw {full_rows} tuples"
        )
        assert cached_rows < NOISE_ROWS, (
            f"{batch} conditional requests read {cached_rows} tuples; a single "
            f"reduction reads about {full_rows // batch}"
        )


@DUAL_BACKEND
def test_a_new_escalation_invalidates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="validator-escalation")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        etag = _pending(ctx["url"]).headers["etag"]

        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "New?"}]})
        assert _pending(ctx["url"], etag=etag).status_code == 200


@DUAL_BACKEND
def test_a_new_marker_invalidates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="validator-marker")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Q?"}]}
        )
        etag = _pending(ctx["url"]).headers["etag"]

        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{decision['event_id']}/dismiss", timeout=10
        )
        assert _pending(ctx["url"], etag=etag).status_code == 200


@DUAL_BACKEND
def test_an_execution_turning_terminal_invalidates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="validator-terminal")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "Q?"}]})
        etag = _pending(ctx["url"]).headers["etag"]

        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
            )
            conn.commit()
        assert _pending(ctx["url"], etag=etag).status_code == 200


@DUAL_BACKEND
def test_an_execution_entering_the_active_set_invalidates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="validator-active-set")
        etag = _pending(ctx["url"]).headers["etag"]

        create_execution_via_api(ctx["url"], agent_id, "new execution")
        assert _pending(ctx["url"], etag=etag).status_code == 200


@DUAL_BACKEND
def test_pending_briefs_are_full_and_carry_no_dead_enrichment(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="pending-shape")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "task", title="Titled"
        )
        decision = rest_escalate_ok(
            ctx["url"],
            session_id,
            {
                "questions": [
                    {
                        "question": "Which?",
                        "context": "ctx",
                        "options": [
                            {"label": "A", "description": "a"},
                            {"label": "B", "description": "b"},
                        ],
                    },
                    {"question": "And?"},
                ]
            },
        )

        body = _pending(ctx["url"]).json()
        assert set(body) == {"decisions"}
        brief = next(
            d for d in body["decisions"] if d["event_id"] == decision["event_id"]
        )
        assert set(brief) == {
            "event_id",
            "batch_id",
            "execution_id",
            "execution_title",
            "session_id",
            "status",
            "importance",
            "questions",
            "answer",
            "answered_at",
            "dismissed_at",
            "created_at",
        }
        assert brief["status"] == "pending"
        assert brief["execution_id"] == exec_id
        assert brief["execution_title"] == "Titled"
        assert brief["session_id"] == session_id
        assert len(brief["questions"]) == 2
        assert set(brief["questions"][0]) == {"question", "context", "options", "index"}
        assert "agent_name" not in brief
        assert "hierarchical_name" not in brief


@DUAL_BACKEND
def test_pending_with_a_cursor_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _pending(ctx["url"], before="5")
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "request.invalid"


@DUAL_BACKEND
def test_pending_with_a_page_size_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _pending(ctx["url"], limit=1)
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["code"] == "request.invalid"
        assert "limit" in body["detail"]

        paged = _pending(ctx["url"], state="resolved", limit=1)
        assert paged.status_code == 200


@DUAL_BACKEND
def test_resolved_history_with_an_execution_filter_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="resolved-filter")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        decision = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Q?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{decision['event_id']}/dismiss", timeout=10
        )

        resp = _pending(ctx["url"], state="resolved", execution_id=exec_id)
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "request.invalid"

        assert _pending(ctx["url"], state="resolved").status_code == 200
        assert _pending(ctx["url"], execution_id=exec_id).status_code == 200


@DUAL_BACKEND
def test_escalate_from_a_non_root_session_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lifecycle-root-only")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        child_id = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'child', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (child_id, exec_id, session_id, agent_id),
            )
            conn.commit()

        assert (
            rest_escalate(
                ctx["url"], child_id, {"questions": [{"question": "Q?"}]}
            ).status_code
            == 403
        )


@DUAL_BACKEND
def test_deleting_an_event_less_child_leaves_the_validator_untouched(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lifecycle-cleanup")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "Q?"}]})

        child_id = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'doomed', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (child_id, exec_id, session_id, agent_id),
            )
            conn.commit()

        etag = _pending(ctx["url"]).headers["etag"]

        with db_conn(test_database) as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id = ?", (child_id,)
                ).fetchone()[0]
                == 0
            ), "precondition: the child has events"
            conn.execute("DELETE FROM sessions WHERE id = ?", (child_id,))
            conn.commit()

        assert _pending(ctx["url"], etag=etag).status_code == 304


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_concurrent_commit_in_another_execution_cannot_mask_an_escalation(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="pg-masking")
        exec_x, session_x = create_execution_via_api(ctx["url"], agent_id, "X")
        exec_y, session_y = create_execution_via_api(ctx["url"], agent_id, "Y")

        escalation = json.dumps(
            {
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "data": {
                            "type": "escalate",
                            "batch_id": "masked-batch",
                            "importance": "blocking",
                            "questions": [{"question": "Masked?"}],
                        }
                    }
                ],
            }
        )
        noise = json.dumps({"parts": [{"data": {"type": "turn_complete"}}]})

        held = psycopg2.connect(test_database)
        try:
            with held.cursor() as cur:
                cur.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) "
                    "VALUES (%s, %s, 'platform', %s)",
                    (exec_x, session_x, escalation),
                )

            with db_conn(test_database) as conn:
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) "
                    "VALUES (?, ?, 'platform', ?)",
                    (exec_y, session_y, noise),
                )
                conn.commit()

            etag = _pending(ctx["url"]).headers["etag"]

            held.commit()
        finally:
            held.close()

        resp = _pending(ctx["url"], etag=etag)
        assert resp.status_code == 200, (
            f"expected 200 for a stale ETag, got {resp.status_code}"
        )
        assert any(d["batch_id"] == "masked-batch" for d in resp.json()["decisions"])


@DUAL_BACKEND
def test_a_walk_never_reports_a_pending_decision_as_resolved(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-race")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        settled = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Settled?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{settled['event_id']}/dismiss", timeout=10
        )

        stop = False
        misreported = []

        def escalate_forever():
            i = 0
            while not stop:
                rest_escalate_ok(
                    ctx["url"], session_id, {"questions": [{"question": f"Live {i}?"}]}
                )
                i += 1

        def walk_forever():
            while not stop:
                page = httpx.get(
                    f"{ctx['url']}/api/v1/decisions",
                    params={"state": "resolved", "limit": 50},
                    timeout=30,
                ).json()
                for item in page["items"]:
                    detail = httpx.get(
                        f"{ctx['url']}/api/v1/decisions/{item['event_id']}", timeout=30
                    ).json()
                    if detail["status"] == "pending":
                        misreported.append(item["event_id"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            writers = [pool.submit(escalate_forever) for _ in range(3)]
            readers = [pool.submit(walk_forever) for _ in range(2)]
            time.sleep(6)
            stop = True
            for f in writers + readers:
                f.result()

        assert misreported == [], (
            f"history named these as resolved while they were pending: {misreported}"
        )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_the_resolved_walk_advances_past_a_corrupt_platform_row(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="walk-corrupt")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        older = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Older?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{older['event_id']}/dismiss", timeout=10
        )
        with db_conn(test_database) as conn:
            for _ in range(3):
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) "
                    "VALUES (?, ?, 'platform', ?)",
                    (exec_id, session_id, b"\x80"),
                )
            conn.commit()
        newer = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Newer?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{newer['event_id']}/dismiss", timeout=10
        )

        seen = []
        cursor = None
        for _ in range(20):
            params = {"state": "resolved", "limit": 1}
            if cursor:
                params["before"] = cursor
            page = httpx.get(
                f"{ctx['url']}/api/v1/decisions", params=params, timeout=15
            ).json()
            seen += [i["event_id"] for i in page["items"]]
            if not page["has_more"]:
                break
            assert page["next_cursor"] != cursor, "the cursor must advance"
            cursor = page["next_cursor"]

        assert newer["event_id"] in seen
        assert older["event_id"] in seen, "the corrupt run hid the older decision"


@DUAL_BACKEND
def test_a_pending_row_does_not_consume_a_page_slot(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="slot-efficiency")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resolved = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Resolved?"}]}
        )
        httpx.post(
            f"{ctx['url']}/api/v1/escalate/{resolved['event_id']}/dismiss", timeout=10
        )
        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "Open?"}]})

        page = httpx.get(
            f"{ctx['url']}/api/v1/decisions",
            params={"state": "resolved", "limit": 1},
            timeout=15,
        ).json()
        assert [i["event_id"] for i in page["items"]] == [resolved["event_id"]], (
            "the pending row consumed the only page slot"
        )


@DUAL_BACKEND
def test_the_validator_honours_weak_tags_and_the_wildcard(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="etag-forms")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        rest_escalate_ok(ctx["url"], session_id, {"questions": [{"question": "Q?"}]})

        first = _pending(ctx["url"])
        assert first.status_code == 200
        etag = first.headers["etag"]
        assert not etag.startswith("W/"), "the server mints strong tags"

        for candidate in (etag, f"W/{etag}", "*", f'"other", {etag}'):
            resp = _pending(ctx["url"], etag=candidate)
            assert resp.status_code == 304, (candidate, resp.status_code)

        assert _pending(ctx["url"], etag='"nope"').status_code == 200


@DUAL_BACKEND
def test_an_answered_decision_never_reads_as_expired(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="detail-toctou")

        def answered_then_terminated(index):
            exec_id, session_id = create_execution_via_api(
                ctx["url"], agent_id, f"task {index}"
            )
            decision = rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{index}?"}]}
            )
            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={
                    "parts": [
                        {"text": "answered"},
                        {
                            "data": {
                                "type": "question_answer",
                                "escalation_event_id": decision["event_id"],
                            }
                        },
                    ]
                },
                timeout=15,
            )
            assert resp.status_code == 200, resp.text
            httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=15
            )
            return decision["event_id"]

        event_id = answered_then_terminated(0)
        detail = httpx.get(
            f"{ctx['url']}/api/v1/decisions/{event_id}", timeout=15
        ).json()
        assert detail["status"] == "answered", detail
        assert detail["answer"] == "answered"

        misreported = []
        stop = False

        def read_forever(event_ids):
            while not stop:
                for eid in list(event_ids):
                    body = httpx.get(
                        f"{ctx['url']}/api/v1/decisions/{eid}", timeout=30
                    ).json()
                    if body["status"] == "expired":
                        misreported.append((eid, body["status"]))

        ids = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            reader = pool.submit(read_forever, ids)
            for i in range(1, 9):
                ids.append(answered_then_terminated(i))
            stop = True
            reader.result()

        assert misreported == [], f"answered decisions read as expired: {misreported}"


@DUAL_BACKEND
def test_pending_briefs_are_served_in_ascending_creation_position(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="pending-order")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        written = [
            rest_escalate_ok(
                ctx["url"], session_id, {"questions": [{"question": f"Q{i}?"}]}
            )["event_id"]
            for i in range(6)
        ]

        body = _pending(ctx["url"]).json()
        served = [d["event_id"] for d in body["decisions"]]
        assert served == written, "briefs must arrive in creation position"

        stamps = [d["created_at"] for d in body["decisions"]]
        assert len(set(stamps)) < len(stamps), "expected same-second escalations"
