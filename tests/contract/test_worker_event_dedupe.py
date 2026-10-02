# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import concurrent.futures
import threading
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


def _worker_event(url, db_url, exec_id, session_id, msg_seq, text):
    worker_id = _claim_session(db_url, session_id)
    return httpx.post(
        f"{url}/api/worker/events",
        json={
            "workerId": worker_id,
            "executionId": exec_id,
            "sessionId": session_id,
            "msgSeq": msg_seq,
            "ephemeral": False,
            "payload": {"role": "ROLE_AGENT", "parts": [{"text": text}]},
        },
        timeout=15,
    )


def _claim_session(db_url, session_id, worker_id="dedupe-worker"):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE sessions SET worker_id = ? WHERE id = ?", (worker_id, session_id)
        )
        conn.commit()
    return worker_id


def _message_count(db_url, session_id, msg_seq):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND msg_seq = ?",
            (session_id, msg_seq),
        ).fetchone()[0]


@DUAL_BACKEND
def test_a_duplicate_worker_event_is_harmless(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="dedupe")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        first = _worker_event(
            ctx["url"], test_database, exec_id, session_id, 41, "hello"
        )
        assert first.status_code == 201, first.text
        assert _message_count(test_database, session_id, 41) == 1

        again = _worker_event(
            ctx["url"], test_database, exec_id, session_id, 41, "hello again"
        )
        assert again.status_code == 201, again.text
        assert _message_count(test_database, session_id, 41) == 1


@DUAL_BACKEND
def test_distinct_sequence_numbers_all_land(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="dedupe-distinct")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        for seq in range(60, 70):
            assert (
                _worker_event(
                    ctx["url"], test_database, exec_id, session_id, seq, f"m{seq}"
                ).status_code
                == 201
            )
        with db_conn(test_database) as conn:
            rows = conn.execute(
                "SELECT id FROM events WHERE session_id = ? AND msg_seq >= 60 "
                "ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        ids = [r[0] for r in rows]
        assert len(ids) == 10
        assert ids == sorted(ids), "event ids should be monotonically increasing"


@DUAL_BACKEND
def test_identical_user_messages_are_not_deduplicated(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="identical-messages-agent")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for _ in range(2):
            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "same text"}]},
                timeout=15,
            )
            assert resp.status_code == 200, resp.text
        with db_conn(test_database) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? "
                "AND event_type = 'message' AND payload LIKE ?",
                (session_id, "%same text%"),
            ).fetchone()[0]
        assert count == 2


def _post_event(url, exec_id, session_id, worker_id, msg_seq, text, timeout=30):
    return httpx.post(
        f"{url}/api/worker/events",
        json={
            "workerId": worker_id,
            "executionId": exec_id,
            "sessionId": session_id,
            "msgSeq": msg_seq,
            "ephemeral": False,
            "payload": {"role": "ROLE_AGENT", "parts": [{"text": text}]},
        },
        timeout=timeout,
    )


def _payload_at(db_url, session_id, msg_seq):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND msg_seq = ?",
            (session_id, msg_seq),
        ).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_worker_that_loses_the_session_mid_write_takes_no_sequence_number(
    test_database,
):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="dedupe-stale-owner")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _claim_session(test_database, session_id, "worker-A")

        result = {}

        def fire_a():
            result["response"] = _post_event(
                ctx["url"], exec_id, session_id, "worker-A", 91, "STALE-A"
            )

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )

            thread = threading.Thread(target=fire_a)
            thread.start()
            time.sleep(2)
            assert thread.is_alive(), "worker A was expected to block on the lock"
            assert _payload_at(test_database, session_id, 91) is None

            holder.execute(
                "UPDATE sessions SET worker_id = ? WHERE id = ?",
                ("worker-B", session_id),
            )
            holder.commit()

        thread.join(timeout=30)
        assert not thread.is_alive()

        assert result["response"].status_code == 200, result["response"].text
        assert _payload_at(test_database, session_id, 91) is None

        owner = _post_event(ctx["url"], exec_id, session_id, "worker-B", 91, "OWNER-B")
        assert owner.status_code == 201, owner.text
        stored = _payload_at(test_database, session_id, 91)
        assert stored is not None
        assert "OWNER-B" in stored
        assert "STALE-A" not in stored
        assert _message_count(test_database, session_id, 91) == 1


@DUAL_BACKEND
def test_a_reassigned_sequence_number_never_holds_the_old_owner_payload(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="dedupe-stale-rounds")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        for seq in range(120, 132):
            _claim_session(test_database, session_id, "worker-A")

            def flip_and_write():
                _claim_session(test_database, session_id, "worker-B")
                return _post_event(
                    ctx["url"], exec_id, session_id, "worker-B", seq, "OWNER-B"
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                fa = pool.submit(
                    _post_event,
                    ctx["url"],
                    exec_id,
                    session_id,
                    "worker-A",
                    seq,
                    "STALE-A",
                )
                fb = pool.submit(flip_and_write)
                ra, rb = fa.result(), fb.result()

            assert ra.status_code in (200, 201), ra.text
            assert rb.status_code == 201, rb.text
            stored = _payload_at(test_database, session_id, seq)
            assert _message_count(test_database, session_id, seq) == 1
            if ra.status_code == 200:
                assert "OWNER-B" in stored, stored


class _StreamReader:
    def __init__(self, url, exec_id):
        self._url = f"{url}/api/v1/executions/{exec_id}/events/stream"
        self.lines = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with httpx.stream("GET", self._url, timeout=30) as response:
                for line in response.iter_lines():
                    self.lines.append(line)
                    if self._stop.is_set():
                        return
        except httpx.HTTPError:
            return

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if any(line.startswith("event: position") for line in self.lines):
                return self
            time.sleep(0.05)
        raise AssertionError("the stream never announced its position")

    def __exit__(self, *_exc):
        self._stop.set()

    def ephemerals(self):
        return [line for line in self.lines if line.startswith("event: ephemeral")]

    def wait_for_ephemeral(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ephemerals():
                return True
            time.sleep(0.05)
        return False


@DUAL_BACKEND
def test_a_replaced_worker_cannot_stream_into_the_session_it_lost(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="ephemeral-owner")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _claim_session(test_database, session_id, "worker-A")

        with _StreamReader(ctx["url"], exec_id) as stream:
            _claim_session(test_database, session_id, "worker-B")

            stale = httpx.post(
                f"{ctx['url']}/api/worker/events",
                json={
                    "workerId": "worker-A",
                    "executionId": exec_id,
                    "sessionId": session_id,
                    "msgSeq": 200,
                    "ephemeral": True,
                    "payload": {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "STALE-DELTA"}],
                    },
                },
                timeout=15,
            )
            assert stale.status_code == 200, stale.text
            assert not stream.wait_for_ephemeral(timeout=3)

            owner = httpx.post(
                f"{ctx['url']}/api/worker/events",
                json={
                    "workerId": "worker-B",
                    "executionId": exec_id,
                    "sessionId": session_id,
                    "msgSeq": 201,
                    "ephemeral": True,
                    "payload": {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "OWNER-DELTA"}],
                    },
                },
                timeout=15,
            )
            assert owner.status_code == 201, owner.text
            assert stream.wait_for_ephemeral(timeout=10)

        body = "\n".join(stream.lines)
        assert "OWNER-DELTA" in body
        assert "STALE-DELTA" not in body
        assert _message_count(test_database, session_id, 200) == 0
        assert _message_count(test_database, session_id, 201) == 0
