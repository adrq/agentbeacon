# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import threading
import time
import uuid

import httpx

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.sse_helpers import (
    _persisted,
    _position,
    _stream_sse_events,
)


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
    time.sleep(1.0)
    return collected, done


@DUAL_BACKEND
def test_a_mid_stream_terminal_is_forwarded_then_the_stream_closes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-terminal")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        collected, done = _stream_in_background(ctx["url"], exec_id)
        started = time.time()
        assert (
            httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
            ).status_code
            == 200
        )
        assert done.wait(timeout=20), "the stream did not close on a terminal event"
        assert time.time() - started < 15

        terminal = [
            e
            for e in _persisted(collected)
            if e["data"]["event_type"] == "state_change"
            and e["data"]["session_id"] is None
            and e["data"]["payload"].get("outcome")
            in ("completed", "failed", "canceled")
        ]
        assert len(terminal) == 1, "the reason arrives on the stream, exactly once"
        assert [
            e["event"] for e in collected if e["event"] not in (None, "position")
        ] == []


@DUAL_BACKEND
def test_a_session_level_terminal_does_not_end_the_stream(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-session-terminal")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        child_id = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'sse-child', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (child_id, exec_id, session_id, agent_id),
            )
            conn.commit()

        collected, done = _stream_in_background(ctx["url"], exec_id, timeout=6.0)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=10
        )
        assert resp.status_code == 200, resp.text

        assert done.wait(timeout=20)
        session_terminals = [
            e
            for e in _persisted(collected)
            if e["data"]["event_type"] == "state_change"
            and e["data"]["session_id"] is not None
        ]
        assert session_terminals, "the session terminal was delivered"


@DUAL_BACKEND
def test_terminal_at_connect_is_silence_not_an_error(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-terminal-at-connect")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        assert (
            httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10
            ).status_code
            == 200
        )

        events = _stream_sse_events(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream", timeout=3.0
        )
        assert _position(events)["position"] is not None
        assert _persisted(events) == []
        assert [e["event"] for e in events if e["event"] == "protocol_error"] == []


@DUAL_BACKEND
def test_the_terminal_event_is_the_last_thing_on_the_wire(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-terminal-last")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        collected, done = _stream_in_background(ctx["url"], exec_id, timeout=25.0)

        terminal = json.dumps({"desired": "terminate", "outcome": "canceled"})
        trailing = json.dumps({"parts": [{"data": {"type": "turn_complete"}}]})
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, NULL, 'state_change', ?)",
                (exec_id, terminal),
            )
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, trailing),
            )
            rows = conn.execute(
                "SELECT id FROM events WHERE execution_id = ? ORDER BY id DESC LIMIT 2",
                (exec_id,),
            ).fetchall()
            conn.commit()
        trailing_id, terminal_id = str(rows[0][0]), str(rows[1][0])

        assert done.wait(timeout=25), "the stream did not close on the terminal event"

        delivered = [e["data"]["id"] for e in _persisted(collected)]
        assert delivered, "the terminal event should have been delivered"
        assert delivered[-1] == terminal_id, (
            f"expected the terminal event last on the wire, got {delivered}"
        )
        assert trailing_id not in delivered
