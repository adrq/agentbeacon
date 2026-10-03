# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import threading
import time

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import rest_escalate_ok
from tests.sse_helpers import (
    _parse_sse_events,
    _persisted,
    _position,
    _stream_sse_events,
)

CATCH_UP_SECONDS = 15


def _history(url, exec_id, before=None):
    params = {"limit": 500}
    if before is not None:
        params["before"] = before
    resp = httpx.get(
        f"{url}/api/v1/executions/{exec_id}/events", params=params, timeout=30
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _stream_in_background(url, exec_id, timeout=25.0):
    collected = []
    done = threading.Event()

    def run():
        collected.extend(
            _stream_sse_events(
                f"{url}/api/v1/executions/{exec_id}/events/stream", timeout=timeout
            )
        )
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return collected, done


@DUAL_BACKEND
def test_the_seam_delivers_the_watermark_row(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="seam")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        newest = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Newest?"}]}
        )

        events = _stream_sse_events(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream", timeout=3.0
        )
        seam = _position(events)
        assert seam["position"] == newest["event_id"]

        page = _history(ctx["url"], exec_id, before=seam["history_before"])
        assert page[-1]["id"] == seam["position"], (
            "the history page before the seam does not end at the position row"
        )


@DUAL_BACKEND
def test_an_empty_stream_reports_null_and_pages_from_the_top(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO executions (id, context_id, metadata, max_depth, "
                "max_width, sandbox_policy, created_at, updated_at) VALUES "
                "('empty-exec', 'ctx', '{}', 3, 5, "
                '\'{"fs_level":"unrestricted"}\', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)'
            )
            conn.commit()

        events = _stream_sse_events(
            f"{ctx['url']}/api/v1/executions/empty-exec/events/stream", timeout=3.0
        )
        seam = _position(events)
        assert seam["position"] is None
        assert seam["history_before"] is None
        assert _history(ctx["url"], "empty-exec") == []


@DUAL_BACKEND
def test_no_event_is_lost_across_the_splice_seam(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="seam-race")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        for _ in range(6):
            collected, done = _stream_in_background(ctx["url"], exec_id, timeout=6.0)
            written = [
                rest_escalate_ok(
                    ctx["url"], session_id, {"questions": [{"question": "Race?"}]}
                )["event_id"]
                for _ in range(3)
            ]
            assert done.wait(timeout=20)

            seam = _position(collected)
            live = {e["data"]["id"] for e in _persisted(collected)}
            history = {
                e["id"]
                for e in _history(ctx["url"], exec_id, before=seam["history_before"])
            }
            for event_id in written:
                assert event_id in live or event_id in history, (
                    f"{event_id} fell into the seam"
                )


@DUAL_BACKEND
def test_a_zero_id_notification_still_delivers_its_rows(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="zero-id-wake")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        collected, done = _stream_in_background(ctx["url"], exec_id, timeout=8.0)
        time.sleep(1.0)

        written = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Woken?"}]}
        )["event_id"]
        assert done.wait(timeout=20)

        delivered = {e["data"]["id"] for e in _persisted(collected)}
        assert written in delivered, (
            "the commit behind the zero-id wake was not delivered"
        )


@DUAL_BACKEND
def test_a_commit_with_no_broadcast_is_still_delivered(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="no-broadcast")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        frames = []
        stop = threading.Event()

        def read_until_stop():
            lines = []
            try:
                with httpx.stream(
                    "GET",
                    f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                    timeout=CATCH_UP_SECONDS + 20,
                ) as resp:
                    for line in resp.iter_lines():
                        lines.append(line)
                        if stop.is_set():
                            break
            except httpx.ReadTimeout:
                pass
            frames.extend(_parse_sse_events(lines))

        reader = threading.Thread(target=read_until_stop, daemon=True)
        reader.start()
        time.sleep(1.0)

        payload = json.dumps({"parts": [{"data": {"type": "turn_complete"}}]})
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, payload),
            )
            row = conn.execute(
                "SELECT MAX(id) FROM events WHERE execution_id = ?", (exec_id,)
            ).fetchone()
            conn.commit()
        silent_id = str(row[0])

        time.sleep(CATCH_UP_SECONDS + 6)
        stop.set()
        reader.join(timeout=CATCH_UP_SECONDS + 20)

        delivered = {e["data"]["id"] for e in _persisted(frames)}
        assert silent_id in delivered, (
            "the row written without a broadcast was not delivered"
        )


@DUAL_BACKEND
def test_reconnecting_re_splices_with_no_gaps_or_duplicates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="resplice")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        rendered = set()
        for round_index in range(4):
            events = _stream_sse_events(
                f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream", timeout=2.5
            )
            seam = _position(events)
            rendered = {
                e["id"]
                for e in _history(ctx["url"], exec_id, before=seam["history_before"])
            }
            rendered |= {e["data"]["id"] for e in _persisted(events)}

            rest_escalate_ok(
                ctx["url"],
                session_id,
                {"questions": [{"question": f"Round {round_index}?"}]},
            )

        final = _stream_sse_events(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream", timeout=2.5
        )
        seam = _position(final)
        history = _history(ctx["url"], exec_id, before=seam["history_before"])
        ids = [e["id"] for e in history]
        assert len(ids) == len(set(ids)), "no duplicates after dedupe-by-id"

        with db_conn(test_database) as conn:
            committed = [
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM events WHERE execution_id = ? ORDER BY id ASC",
                    (exec_id,),
                ).fetchall()
            ]
        assert ids == committed, "no gaps"


@DUAL_BACKEND
def test_an_ephemeral_wake_also_carries_the_rows_behind_it(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="ephemeral-wake")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'wake-worker' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        frames = []
        stop = threading.Event()

        def read_until_stop():
            lines = []
            try:
                with httpx.stream(
                    "GET",
                    f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                    timeout=CATCH_UP_SECONDS + 20,
                ) as resp:
                    for line in resp.iter_lines():
                        lines.append(line)
                        if stop.is_set():
                            break
            except httpx.ReadTimeout:
                pass
            frames.extend(_parse_sse_events(lines))

        reader = threading.Thread(target=read_until_stop, daemon=True)
        reader.start()
        time.sleep(1.0)

        payload = json.dumps({"parts": [{"data": {"type": "turn_complete"}}]})
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, payload),
            )
            row = conn.execute(
                "SELECT MAX(id) FROM events WHERE execution_id = ?", (exec_id,)
            ).fetchone()
            conn.commit()
        silent_id = str(row[0])

        assert (
            httpx.post(
                f"{ctx['url']}/api/worker/events",
                json={
                    "workerId": "wake-worker",
                    "executionId": exec_id,
                    "sessionId": session_id,
                    "msgSeq": 700,
                    "ephemeral": True,
                    "payload": {"role": "ROLE_AGENT", "parts": [{"text": "delta"}]},
                },
                timeout=15,
            ).status_code
            == 201
        )

        deadline = time.time() + (CATCH_UP_SECONDS - 6)
        while time.time() < deadline:
            if silent_id in {e["data"]["id"] for e in _persisted(frames)}:
                break
            time.sleep(0.2)
        stop.set()
        reader.join(timeout=CATCH_UP_SECONDS + 20)

        delivered = {e["data"]["id"] for e in _persisted(frames)}
        assert silent_id in delivered, (
            "the ephemeral wake returned without reading past the cursor"
        )
