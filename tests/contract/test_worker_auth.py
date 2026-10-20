# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil
import psycopg2
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.mock_agent_helpers import (
    make_executor_report,
    set_session_fields,
    make_turn_result,
    seed_acp_mock_agent,
    seed_codex_test_agent,
    start_mock_scheduler,
)
from tests.testhelpers import (
    admit_worker,
    admit_worker_with_token,
    anonymous_headers,
    assert_forbidden,
    assert_unauthorized,
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    orchestrator_context,
    PortManager,
    scheduler_context,
    seed_test_agent,
    start_worker,
    wait_for_port,
)
from tests.sse_helpers import _stream_sse_events
from tests.worker_test_helpers import enqueue_session, mark_complete, poll_until

BASE_DIR = Path(__file__).parent.parent.parent
WORKER_BIN = BASE_DIR / "bin" / "agentbeacon-worker"
_MOCK_SDKS = BASE_DIR / "tests" / "mock_sdks"
EXECUTOR_ENV = {
    "AGENTBEACON_EXECUTORS_DIR": str(_MOCK_SDKS / "executors"),
    "NODE_OPTIONS": "--preserve-symlinks --preserve-symlinks-main",
}


def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _bearer(token):
    return anonymous_headers({"Authorization": f"Bearer {token}"})


def _sync(url, worker_id, token, body=None, timeout=30):
    return httpx.post(
        f"{url}/api/worker/sync",
        json={"worker_id": worker_id, **(body or {})},
        headers=_bearer(token),
        timeout=timeout,
    )


def _rotate(url, token):
    return httpx.post(f"{url}/api/worker/token", headers=_bearer(token), timeout=30)


def _worker_rows(db_url, worker_id):
    with db_conn(db_url) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT token_hash FROM credentials "
                "WHERE principal_type = 'worker' AND principal_id = ?",
                (worker_id,),
            ).fetchall()
        ]


def _stale_report(url, worker_id, token, session_id):
    return _sync(
        url,
        worker_id,
        token,
        {"executor_report": make_executor_report(session_id, "running")},
    )


def _idle_session(ctx, agent_id):
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "idle")
    with db_conn(ctx["db_url"]) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
        conn.commit()
    return exec_id, session_id


def _wait_for_pg_lock_waiter(db_url, timeout=10):
    deadline = time.time() + timeout
    with psycopg2.connect(db_url) as observer:
        observer.autocommit = True
        while time.time() < deadline:
            with observer.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cur.fetchone()[0] > 0:
                    return
            time.sleep(0.05)
    raise AssertionError("no request waited on the held lock")


@DUAL_BACKEND
def test_admit_returns_a_minted_worker_id_and_token(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(f"{ctx['url']}/api/v1/workers", timeout=10)
        assert resp.status_code == 201
        body = resp.json()
        assert set(body) == {"worker_id", "token"}
        assert str(uuid.UUID(body["worker_id"])) == body["worker_id"]
        assert _worker_rows(test_database, body["worker_id"]) == [
            _sha256(body["token"])
        ]
        assert _sync(ctx["url"], body["worker_id"], body["token"]).status_code == 200


@DUAL_BACKEND
def test_admit_refuses_a_caller_chosen_worker_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        chosen = str(uuid.uuid4())
        resp = httpx.post(
            f"{ctx['url']}/api/v1/workers", json={"worker_id": chosen}, timeout=10
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "request.invalid"
        with db_conn(test_database) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM credentials WHERE principal_type = 'worker'"
            ).fetchone()[0]
        assert count == 0


@DUAL_BACKEND
def test_admit_is_human_only(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        worker_id, token = admit_worker_with_token(ctx["url"])
        by_worker = httpx.post(
            f"{ctx['url']}/api/v1/workers", headers=_bearer(token), timeout=10
        )
        assert_forbidden(by_worker)
        anonymous = httpx.post(
            f"{ctx['url']}/api/v1/workers", headers=anonymous_headers(), timeout=10
        )
        assert_unauthorized(anonymous)


@DUAL_BACKEND
def test_rotation_returns_new_token_and_kills_the_old(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        worker_id, old = admit_worker_with_token(ctx["url"])
        rotated = _rotate(ctx["url"], old)
        assert rotated.status_code == 200
        assert set(rotated.json()) == {"token"}
        new = rotated.json()["token"]
        assert _worker_rows(test_database, worker_id) == [_sha256(new)]
        assert_unauthorized(_sync(ctx["url"], worker_id, old))
        assert_unauthorized(_rotate(ctx["url"], old))
        assert _sync(ctx["url"], worker_id, new).status_code == 200


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_rotation_mints_nothing_when_its_token_is_consumed_after_authentication(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        worker_id, token = admit_worker_with_token(ctx["url"])
        result = {}
        holder = psycopg2.connect(test_database)
        cur = holder.cursor()
        cur.execute(
            "SELECT token_hash FROM credentials WHERE token_hash = %s FOR UPDATE",
            (_sha256(token),),
        )
        thread = threading.Thread(
            target=lambda: result.update(r=_rotate(ctx["url"], token))
        )
        thread.start()
        _wait_for_pg_lock_waiter(test_database)
        cur.execute("DELETE FROM credentials WHERE token_hash = %s", (_sha256(token),))
        holder.commit()
        holder.close()
        thread.join(timeout=30)
        assert_unauthorized(result["r"])
        assert _worker_rows(test_database, worker_id) == []


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_assign_parked_on_execution_lock_reaches_no_killed_worker(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="parked-assign")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _, idle_session = _idle_session(ctx, agent_id)
        worker_id, token = admit_worker_with_token(ctx["url"])
        result = {}

        holder = psycopg2.connect(test_database)
        cur = holder.cursor()
        cur.execute("SELECT id FROM executions WHERE id = %s FOR UPDATE", (exec_id,))
        thread = threading.Thread(
            target=lambda: result.update(r=_sync(ctx["url"], worker_id, token))
        )
        thread.start()
        _wait_for_pg_lock_waiter(test_database)
        stale = _stale_report(ctx["url"], worker_id, token, idle_session)
        assert stale.status_code == 200
        holder.commit()
        holder.close()
        thread.join(timeout=30)

        resp = result["r"]
        assert_unauthorized(resp)
        assert "session_token" not in resp.text
        with db_conn(test_database) as conn:
            row = conn.execute(
                "SELECT command_type, worker_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert tuple(row) == ("assign", worker_id)


def _event(url, worker_id, token, exec_id, session_id, msg_seq):
    return httpx.post(
        f"{url}/api/worker/events",
        json={
            "workerId": worker_id,
            "executionId": exec_id,
            "sessionId": session_id,
            "msgSeq": msg_seq,
            "ephemeral": False,
            "payload": {"role": "ROLE_AGENT", "parts": [{"text": f"m{msg_seq}"}]},
        },
        headers=_bearer(token),
        timeout=30,
    )


def _event_seqs(db_url, session_id):
    with db_conn(db_url) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT msg_seq FROM events WHERE session_id = ? AND msg_seq IS NOT NULL "
                "ORDER BY msg_seq",
                (session_id,),
            ).fetchall()
        ]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
@pytest.mark.parametrize("cause", ["kill", "rotation"])
def test_event_parked_on_execution_lock_persists_nothing_once_credential_ends(
    test_database, cause
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="parked-event")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _, idle_session = _idle_session(ctx, agent_id)
        worker_id, token = admit_worker_with_token(ctx["url"])
        assign = _sync(ctx["url"], worker_id, token)
        assert assign.status_code == 200, assign.text
        assert assign.json()["type"] == "command"
        first = _event(ctx["url"], worker_id, token, exec_id, session_id, 1)
        assert first.status_code == 201, first.text
        result = {}

        holder = psycopg2.connect(test_database)
        cur = holder.cursor()
        cur.execute("SELECT id FROM executions WHERE id = %s FOR UPDATE", (exec_id,))
        thread = threading.Thread(
            target=lambda: result.update(
                r=_event(ctx["url"], worker_id, token, exec_id, session_id, 2)
            )
        )
        thread.start()
        _wait_for_pg_lock_waiter(test_database)
        if cause == "kill":
            ended = _stale_report(ctx["url"], worker_id, token, idle_session)
            assert ended.status_code == 200
        else:
            ended = _rotate(ctx["url"], token)
            assert ended.status_code == 200, ended.text
        assert_unauthorized(_sync(ctx["url"], worker_id, token))
        holder.commit()
        holder.close()
        thread.join(timeout=30)

        resp = result["r"]
        assert_unauthorized(resp)
        assert _event_seqs(test_database, session_id) == [1]
        if cause == "rotation":
            retried = _event(
                ctx["url"], worker_id, ended.json()["token"], exec_id, session_id, 2
            )
            assert retried.status_code == 201, retried.text
            assert _event_seqs(test_database, session_id) == [1, 2]


def _assigned_worker(ctx, name):
    agent_id = seed_test_agent(ctx["db_url"], name=name)
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
    _, idle_session = _idle_session(ctx, agent_id)
    worker_id, token = admit_worker_with_token(ctx["url"])
    assign = _sync(ctx["url"], worker_id, token)
    assert assign.status_code == 200, assign.text
    assert assign.json()["action"]["type"] == "assign"
    return exec_id, session_id, idle_session, worker_id, token, assign.json()["token"]


def _end_credential(ctx, cause, worker_id, token, idle_session):
    if cause == "kill":
        assert (
            _stale_report(ctx["url"], worker_id, token, idle_session).status_code == 200
        )
        new_token = None
    else:
        rotated = _rotate(ctx["url"], token)
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["token"]
    assert_unauthorized(_sync(ctx["url"], worker_id, token))
    return new_token


def _parked(ctx, lock_sql, lock_id, send, end):
    result = {}
    holder = psycopg2.connect(ctx["db_url"])
    try:
        holder.cursor().execute(lock_sql, (lock_id,))
        thread = threading.Thread(target=lambda: result.update(r=send()))
        thread.start()
        _wait_for_pg_lock_waiter(ctx["db_url"])
        ended = end()
        holder.commit()
    finally:
        holder.close()
    thread.join(timeout=30)
    return result["r"], ended


def _session_row(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT executor_state, agent_session_id, command_token FROM sessions "
            "WHERE id = ?",
            (session_id,),
        ).fetchone()
    return tuple(row)


def _state_change_count(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'state_change'",
            (session_id,),
        ).fetchone()[0]


_LOCK_EXECUTION = "SELECT id FROM executions WHERE id = %s FOR UPDATE"
_LOCK_SESSION = "SELECT id FROM sessions WHERE id = %s FOR UPDATE"


def _assert_unauthorized(resp):
    assert_unauthorized(resp)


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
@pytest.mark.parametrize("cause", ["kill", "rotation"])
@pytest.mark.parametrize("held", ["execution", "session"])
def test_parked_executor_report_writes_nothing_once_credential_ends(
    test_database, cause, held
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, idle, worker_id, token, _ = _assigned_worker(
            ctx, "parked-report"
        )
        before = _session_row(test_database, session_id)
        assert before[0] != "running"

        def report(with_token):
            return _sync(
                ctx["url"],
                worker_id,
                with_token,
                {"executor_report": make_executor_report(session_id, "running")},
            )

        lock_sql, lock_id = (
            (_LOCK_EXECUTION, exec_id)
            if held == "execution"
            else (_LOCK_SESSION, session_id)
        )
        events_before = _state_change_count(test_database, session_id)
        resp, new_token = _parked(
            ctx,
            lock_sql,
            lock_id,
            lambda: report(token),
            lambda: _end_credential(ctx, cause, worker_id, token, idle),
        )
        _assert_unauthorized(resp)
        assert _session_row(test_database, session_id) == before
        assert _state_change_count(test_database, session_id) == events_before
        if cause == "rotation":
            assert report(new_token).status_code == 200
            assert _session_row(test_database, session_id)[0] == "running"
            assert _state_change_count(test_database, session_id) == events_before + 1


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
@pytest.mark.parametrize("cause", ["kill", "rotation"])
def test_parked_agent_session_id_update_writes_nothing_once_credential_ends(
    test_database, cause
):
    with scheduler_context(db_url=test_database) as ctx:
        _, session_id, idle, worker_id, token, _ = _assigned_worker(
            ctx, "parked-agent-session"
        )
        before = _session_row(test_database, session_id)
        assert before[1] is None

        def report(with_token):
            return _sync(
                ctx["url"],
                worker_id,
                with_token,
                {
                    "executor_report": make_executor_report(
                        session_id, "running", "agent-session-1"
                    )
                },
            )

        resp, new_token = _parked(
            ctx,
            _LOCK_SESSION,
            session_id,
            lambda: report(token),
            lambda: _end_credential(ctx, cause, worker_id, token, idle),
        )
        _assert_unauthorized(resp)
        assert _session_row(test_database, session_id) == before
        if cause == "rotation":
            assert report(new_token).status_code == 200
            assert _session_row(test_database, session_id)[:2] == (
                "running",
                "agent-session-1",
            )


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
@pytest.mark.parametrize("cause", ["kill", "rotation"])
def test_parked_turn_result_writes_nothing_once_credential_ends(test_database, cause):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, idle, worker_id, token, _ = _assigned_worker(
            ctx, "parked-result"
        )
        result = make_turn_result(
            session_id,
            messages=[
                {"msg_seq": 1, "role": "ROLE_AGENT", "parts": [{"text": "done"}]}
            ],
        )

        def send(with_token):
            return _sync(ctx["url"], worker_id, with_token, {"turn_result": result})

        resp, new_token = _parked(
            ctx,
            _LOCK_EXECUTION,
            exec_id,
            lambda: send(token),
            lambda: _end_credential(ctx, cause, worker_id, token, idle),
        )
        _assert_unauthorized(resp)
        assert _event_seqs(test_database, session_id) == []
        if cause == "rotation":
            assert send(new_token).status_code == 200
            assert _event_seqs(test_database, session_id) == [1]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
@pytest.mark.parametrize("cause", ["kill", "rotation"])
def test_parked_command_ack_clears_nothing_once_credential_ends(test_database, cause):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, session_id, idle, worker_id, token, command = _assigned_worker(
            ctx, "parked-ack"
        )
        assert _session_row(test_database, session_id)[2] == command

        def ack(with_token):
            return _sync(ctx["url"], worker_id, with_token, {"command_ack": command})

        resp, new_token = _parked(
            ctx,
            _LOCK_EXECUTION,
            exec_id,
            lambda: ack(token),
            lambda: _end_credential(ctx, cause, worker_id, token, idle),
        )
        _assert_unauthorized(resp)
        assert _session_row(test_database, session_id)[2] == command
        if cause == "rotation":
            assert ack(new_token).status_code == 200
            assert _session_row(test_database, session_id)[2] is None


def _ephemeral(url, worker_id, token, exec_id, session_id, msg_seq):
    return httpx.post(
        f"{url}/api/worker/events",
        json={
            "workerId": worker_id,
            "executionId": exec_id,
            "sessionId": session_id,
            "msgSeq": msg_seq,
            "ephemeral": True,
            "payload": {"role": "ROLE_AGENT", "parts": [{"text": f"d{msg_seq}"}]},
        },
        headers=_bearer(token),
        timeout=30,
    )


@pytest.mark.parametrize("cause", ["kill", "rotation"])
def test_paused_ephemeral_event_publishes_nothing_once_credential_ends(cause):
    env = {"AGENTBEACON_TEST_PAUSE_AT": "worker_event_publish:2000"}
    with scheduler_context(env=env) as ctx:
        exec_id, session_id, idle, worker_id, token, _ = _assigned_worker(
            ctx, "paused-ephemeral"
        )
        frames = []
        stream = threading.Thread(
            target=lambda: frames.extend(
                _stream_sse_events(
                    f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                    timeout=8.0,
                )
            )
        )
        stream.start()
        time.sleep(0.5)

        first = _ephemeral(ctx["url"], worker_id, token, exec_id, session_id, 1)
        assert first.status_code == 201, first.text

        result = {}
        sender = threading.Thread(
            target=lambda: result.update(
                r=_ephemeral(ctx["url"], worker_id, token, exec_id, session_id, 2)
            )
        )
        sender.start()
        time.sleep(0.5)
        _end_credential(ctx, cause, worker_id, token, idle)
        sender.join(timeout=30)
        stream.join(timeout=30)

        _assert_unauthorized(result["r"])
        published = [f["data"]["msg_seq"] for f in frames if f["event"] == "ephemeral"]
        assert published == [1]


class _HeldRequest:
    def __init__(self, url, path, token, body):
        parsed = urlparse(url)
        self._data = json.dumps(body).encode()
        head = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            f"Authorization: Bearer {token}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(self._data)}\r\n"
            "Expect: 100-continue\r\n"
            "Connection: close\r\n\r\n"
        )
        self._sock = socket.create_connection(
            (parsed.hostname, parsed.port), timeout=30
        )
        self._sock.sendall(head.encode())
        interim = b""
        while b"\r\n\r\n" not in interim:
            chunk = self._sock.recv(4096)
            assert chunk, interim
            interim += chunk
        assert interim.startswith(b"HTTP/1.1 100 "), interim

    def release(self):
        with self._sock as sock:
            sock.sendall(self._data)
            raw = b""
            while chunk := sock.recv(4096):
                raw += chunk
        return int(raw.split(b" ", 2)[1]), raw


def _registered(url, worker_id, token):
    resp = httpx.post(
        f"{url}/api/worker/events",
        json={
            "workerId": f"not-{worker_id}",
            "executionId": "probe",
            "sessionId": "probe",
            "msgSeq": 1,
            "ephemeral": True,
            "payload": {},
        },
        headers=_bearer(token),
        timeout=10,
    )
    assert resp.status_code in (401, 403), resp.text
    return resp.status_code == 403


def test_rejected_sync_with_a_replaced_token_does_not_postpone_age_out():
    env = {
        "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "6",
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "5",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "3",
    }
    with scheduler_context(env=env) as ctx:
        worker_id, t0 = admit_worker_with_token(ctx["url"])
        admitted = time.monotonic()
        held = [
            _HeldRequest(ctx["url"], "/api/worker/sync", t0, {"worker_id": worker_id})
            for _ in range(3)
        ]
        rotated = _rotate(ctx["url"], t0)
        assert rotated.status_code == 200, rotated.text
        t1 = rotated.json()["token"]
        assert _registered(ctx["url"], worker_id, t1)

        for i, request in enumerate(held, start=1):
            time.sleep(max(0.0, admitted + 4 * i - time.monotonic()))
            status, raw = request.release()
            assert status == 401, raw
            assert b'"detail":"a valid credential is required"' in raw

        assert poll_until(
            lambda: not _registered(ctx["url"], worker_id, t1),
            timeout=max(0.0, admitted + 15 - time.monotonic()),
            interval=0.2,
        )


def _send_after_continue(url, path, token, body, between):
    parsed = urlparse(url)
    data = json.dumps(body).encode()
    head = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        f"Authorization: Bearer {token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\n"
        "Expect: 100-continue\r\n"
        "Connection: close\r\n\r\n"
    )
    with socket.create_connection((parsed.hostname, parsed.port), timeout=30) as sock:
        sock.sendall(head.encode())
        interim = b""
        while b"\r\n\r\n" not in interim:
            chunk = sock.recv(4096)
            assert chunk, interim
            interim += chunk
        assert interim.startswith(b"HTTP/1.1 100 "), interim
        between()
        sock.sendall(data)
        raw = b""
        while chunk := sock.recv(4096):
            raw += chunk
    return int(raw.split(b" ", 2)[1]), raw


def _stale_request_with_replaced_token(ctx, path, stale_body):
    worker_id, t0 = admit_worker_with_token(ctx["url"])
    rotated = {}

    def rotate():
        resp = _rotate(ctx["url"], t0)
        assert resp.status_code == 200, resp.text
        rotated["token"] = resp.json()["token"]
        assert _sync(ctx["url"], worker_id, rotated["token"]).status_code == 200

    status, raw = _send_after_continue(
        ctx["url"], path, t0, stale_body(worker_id), rotate
    )
    return worker_id, status, raw, rotated["token"]


@DUAL_BACKEND
def test_stale_report_with_a_replaced_token_leaves_the_worker_registered(test_database):
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": "1"}
    with scheduler_context(db_url=test_database, env=env) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stale-replaced-sync")
        _, idle_session = _idle_session(ctx, agent_id)
        worker_id, status, raw, t1 = _stale_request_with_replaced_token(
            ctx,
            "/api/worker/sync",
            lambda wid: {
                "worker_id": wid,
                "executor_report": make_executor_report(idle_session, "running"),
            },
        )
        assert status == 401, raw
        assert b'"code":"auth.unauthorized"' in raw
        assert b'"detail":"a valid credential is required"' in raw
        resp = _sync(ctx["url"], worker_id, t1)
        assert resp.status_code == 200, resp.text
        assert _stale_report(ctx["url"], worker_id, t1, idle_session).status_code == 200
        assert_unauthorized(_sync(ctx["url"], worker_id, t1))


@DUAL_BACKEND
def test_stale_event_with_a_replaced_token_leaves_the_worker_registered(test_database):
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": "1"}
    with scheduler_context(db_url=test_database, env=env) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stale-replaced-event")
        idle_exec, idle_session = _idle_session(ctx, agent_id)
        worker_id, status, raw, t1 = _stale_request_with_replaced_token(
            ctx,
            "/api/worker/events",
            lambda wid: {
                "workerId": wid,
                "executionId": idle_exec,
                "sessionId": idle_session,
                "msgSeq": 1,
                "ephemeral": False,
                "payload": {"role": "ROLE_AGENT", "parts": [{"text": "late"}]},
            },
        )
        assert status == 401, raw
        assert b'"code":"auth.unauthorized"' in raw
        assert b'"detail":"a valid credential is required"' in raw
        resp = _sync(ctx["url"], worker_id, t1)
        assert resp.status_code == 200, resp.text


@DUAL_BACKEND
def test_long_poll_opened_before_kill_receives_no_assignment(test_database):
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": "10"}
    with scheduler_context(db_url=test_database, env=env) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="poll-kill")
        _, idle_session = _idle_session(ctx, agent_id)
        worker_id, token = admit_worker_with_token(ctx["url"])
        result = {}
        thread = threading.Thread(
            target=lambda: result.update(r=_sync(ctx["url"], worker_id, token))
        )
        thread.start()
        time.sleep(1)
        assert thread.is_alive()
        assert (
            _stale_report(ctx["url"], worker_id, token, idle_session).status_code == 200
        )
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "new work")
        thread.join(timeout=30)

        resp = result["r"]
        assert_unauthorized(resp)
        with db_conn(test_database) as conn:
            row = conn.execute(
                "SELECT worker_id, command_token FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            creds = conn.execute(
                "SELECT COUNT(*) FROM credentials "
                "WHERE principal_type = 'session' AND principal_id = ?",
                (session_id,),
            ).fetchone()[0]
        assert tuple(row) == (None, None)
        assert creds == 0


@DUAL_BACKEND
def test_worker_token_without_registry_entry_is_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        worker_id, token = str(uuid.uuid4()), "unregistered-worker-token"
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO credentials (token_hash, principal_type, principal_id) "
                "VALUES (?, 'worker', ?)",
                (_sha256(token), worker_id),
            )
            conn.commit()
        resp = _sync(ctx["url"], worker_id, token)
        assert_unauthorized(resp)


def test_admitted_worker_survives_liveness_ticks_during_admission():
    env = {
        "AGENTBEACON_TEST_PAUSE_AT": "worker_admit:7000",
        "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "12",
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "5",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "3",
        "AGENTBEACON_LONG_POLL_TIMEOUT_SECS": "1",
    }
    with scheduler_context(env=env) as ctx:
        time.sleep(3.5)
        worker_id, token = admit_worker_with_token(ctx["url"])
        resp = _sync(ctx["url"], worker_id, token)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"type": "no_action"}


@pytest.mark.parametrize("site", ["stale_report", "command_timeout", "heartbeat"])
def test_external_registration_ends_at_each_kill_site(site):
    env = {
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "5",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "3",
    }
    if site == "heartbeat":
        env["AGENTBEACON_HEARTBEAT_TIMEOUT_SECS"] = "1"
    with scheduler_context(env=env) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name=f"kill-{site}")
        worker_id, token = admit_worker_with_token(ctx["url"])
        if site == "stale_report":
            _, idle_session = _idle_session(ctx, agent_id)
            resp = _stale_report(ctx["url"], worker_id, token, idle_session)
            assert resp.status_code == 200
        elif site == "command_timeout":
            _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
            assigned = _sync(ctx["url"], worker_id, token).json()
            assert assigned["action"]["type"] == "assign"
            set_session_fields(
                ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z"
            )
            prober, prober_token = admit_worker_with_token(ctx["url"])
            assert _sync(ctx["url"], prober, prober_token).status_code == 200
            with db_conn(ctx["db_url"]) as conn:
                row = conn.execute(
                    "SELECT executor_state, outcome, worker_id, command_token "
                    "FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            executor_state, outcome, owner, command = tuple(row)
            assert (owner, command) == (None, None)
            assert executor_state == "crashed" or outcome == "failed", tuple(row)
        else:
            time.sleep(12)
        assert_unauthorized(_sync(ctx["url"], worker_id, token))


def test_external_registration_is_lost_on_restart():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_url = f"sqlite:{tmp.name}?mode=rwc"
    try:
        with scheduler_context(db_url=db_url) as ctx:
            worker_id, token = admit_worker_with_token(ctx["url"])
            port = ctx["port"]
        with scheduler_context(db_url=db_url, port=port) as ctx:
            assert_unauthorized(_sync(ctx["url"], worker_id, token))
            again_id, again_token = admit_worker_with_token(ctx["url"])
            assert _sync(ctx["url"], again_id, again_token).status_code == 200
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@DUAL_BACKEND
def test_body_worker_id_mismatch_is_403(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        worker_id, token = admit_worker_with_token(ctx["url"])
        other = str(uuid.uuid4())
        sync = _sync(ctx["url"], other, token)
        assert_forbidden(sync)
        event = httpx.post(
            f"{ctx['url']}/api/worker/events",
            json={
                "workerId": other,
                "executionId": "e",
                "sessionId": "s",
                "msgSeq": 1,
                "payload": {"role": "ROLE_AGENT", "parts": [{"text": "x"}]},
            },
            headers=_bearer(token),
            timeout=10,
        )
        assert_forbidden(event)


def _launch_worker(url, worker_id, token, extra_args=(), extra_env=None):
    env = os.environ.copy()
    env["AGENTBEACON_WORKER_TOKEN"] = token
    env.update(EXECUTOR_ENV)
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            str(WORKER_BIN),
            "--scheduler-url",
            url,
            "--worker-id",
            worker_id,
            "--interval",
            "500ms",
            "--startup-max-attempts",
            "3",
            "--retry-delay",
            "100ms",
            *extra_args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=BASE_DIR,
        env=env,
    )


def test_worker_exits_on_401():
    with scheduler_context() as ctx:
        worker = _launch_worker(ctx["url"], str(uuid.uuid4()), "bogus-token")
        try:
            assert worker.wait(timeout=20) != 0
            output = worker.stdout.read()
            assert "rejected the worker credential" in output
        finally:
            cleanup_processes([worker])


def test_externally_supervised_worker_binary_runs_a_session():
    with scheduler_context() as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"], name="external-run")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        worker_id, token = admit_worker_with_token(ctx["url"])
        worker = _launch_worker(ctx["url"], worker_id, token)
        try:

            def idle():
                with db_conn(ctx["db_url"]) as conn:
                    row = conn.execute(
                        "SELECT executor_state, worker_id FROM sessions WHERE id = ?",
                        (session_id,),
                    ).fetchone()
                return tuple(row) == ("idle", worker_id)

            assert poll_until(idle, timeout=40)
            assert _worker_rows(ctx["db_url"], worker_id) != [_sha256(token)]
            assert len(_worker_rows(ctx["db_url"], worker_id)) == 1
        finally:
            cleanup_processes([worker])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
def test_worker_environ_unreadable():
    with scheduler_context() as ctx:
        worker = start_worker(ctx["url"], interval="500ms")
        try:

            def environ_unreadable():
                try:
                    Path(f"/proc/{worker.pid}/environ").read_bytes()
                except PermissionError:
                    return True
                return False

            assert poll_until(environ_unreadable, timeout=15, interval=0.05)
            assert worker.poll() is None
        finally:
            cleanup_processes([worker])


def _all_worker_rows(db_url):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT token_hash FROM credentials WHERE principal_type = 'worker'"
        ).fetchall()


def test_admitted_workers_do_not_count_toward_max_workers():
    with orchestrator_context(max_workers=1, worker_poll_interval="500ms") as orch:
        admit_worker(orch["url"])
        agent_id = seed_acp_mock_agent(orch["db_url"], name="max-workers")
        create_execution_via_api(orch["url"], agent_id, "hello")

        def local_workers():
            parent = psutil.Process(orch["orchestrator_pid"])
            return [
                c
                for c in parent.children()
                if "agentbeacon-worker" in " ".join(c.cmdline() or [])
            ]

        assert poll_until(lambda: len(local_workers()) == 1, timeout=20)


def test_spawned_worker_survives_slow_registration():
    env = {"AGENTBEACON_TEST_PAUSE_AT": "supervisor_register:1500"}
    with orchestrator_context(
        max_workers=1, worker_poll_interval="500ms", env=env
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"], name="slow-register")
        _, session_id = create_execution_via_api(orch["url"], agent_id, "hello")

        def assigned():
            with db_conn(orch["db_url"]) as conn:
                row = conn.execute(
                    "SELECT worker_id FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
            return row[0] is not None

        assert poll_until(assigned, timeout=40)
        assert len(_all_worker_rows(orch["db_url"])) == 1


def _launch_detached_worker(url, worker_id, token, log_path):
    env = os.environ.copy()
    env["AGENTBEACON_WORKER_TOKEN"] = token
    command = (
        f"'{WORKER_BIN}' --scheduler-url {url} --worker-id {worker_id} --interval 500ms "
        f"--reconnect-max-attempts 200 --retry-delay 100ms > '{log_path}' 2>&1 & echo $!"
    )
    out = subprocess.run(
        ["sh", "-c", command], env=env, cwd=BASE_DIR, capture_output=True, text=True
    )
    return int(out.stdout.strip())


def test_old_workers_rejected_after_scheduler_restart(tmp_path):
    db_url = f"sqlite:{tmp_path / 'restart.db'}?mode=rwc"
    log_path = tmp_path / "worker.log"
    pid = None
    try:
        with scheduler_context(db_url=db_url) as ctx:
            port = ctx["port"]
            worker_id, token = admit_worker_with_token(ctx["url"])
            pid = _launch_detached_worker(ctx["url"], worker_id, token, log_path)
            assert poll_until(
                lambda: _worker_rows(db_url, worker_id) not in ([], [_sha256(token)]),
                timeout=15,
            )
        with scheduler_context(db_url=db_url, port=port):
            assert poll_until(lambda: not _alive(pid), timeout=30)
        assert "rejected the worker credential" in log_path.read_text()
    finally:
        if pid is not None and _alive(pid):
            os.kill(pid, 9)


def _alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _report_from_driver(ctx, agent_id, report_path, worker_env=None):
    _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
    worker = start_worker(
        ctx["url"],
        interval="500ms",
        extra_env={
            **EXECUTOR_ENV,
            "AGENTBEACON_MOCK_REPORT_FILE": report_path,
            **(worker_env or {}),
        },
    )
    try:
        assert poll_until(
            lambda: os.path.exists(report_path) and os.path.getsize(report_path) > 0,
            timeout=40,
        )
        time.sleep(0.2)
        with open(report_path) as f:
            report = json.load(f)
    finally:
        cleanup_processes([worker])
    return session_id, report


def _seed_driver_agent(db_url, driver, user_env=None):
    if driver == "acp":
        agent_id = seed_acp_mock_agent(db_url, name="report-acp")
        if user_env:
            with db_conn(db_url) as conn:
                config = json.loads(
                    conn.execute(
                        "SELECT config FROM agents WHERE id = ?", (agent_id,)
                    ).fetchone()[0]
                )
                config["env"] = user_env
                conn.execute(
                    "UPDATE agents SET config = ? WHERE id = ?",
                    (json.dumps(config), agent_id),
                )
                conn.commit()
        return agent_id
    if driver == "codex_sdk":
        extra = {"env": user_env} if user_env else None
        return seed_codex_test_agent(db_url, name="report-codex", config_extra=extra)
    return seed_test_agent(db_url, name="report-claude", agent_type="claude_sdk")


@pytest.mark.parametrize("driver", ["acp", "codex_sdk", "claude_sdk"])
def test_executor_receives_session_token_env_and_mcp_header(driver, tmp_path):
    with scheduler_context() as ctx:
        agent_id = _seed_driver_agent(ctx["db_url"], driver)
        session_id, report = _report_from_driver(
            ctx, agent_id, str(tmp_path / "report.json")
        )
        with db_conn(ctx["db_url"]) as conn:
            stored = conn.execute(
                "SELECT token_hash FROM credentials "
                "WHERE principal_type = 'session' AND principal_id = ?",
                (session_id,),
            ).fetchone()[0]
        assert report["driver"] == driver
        assert report["session_id"] == session_id
        assert _sha256(report["session_token"]) == stored
        assert report["mcp_authorization"] == f"Bearer {report['session_token']}"


SETUP_ENV = {
    "AGENTBEACON_USERNAME": "setup-user",
    "AGENTBEACON_PASSWORD": "setup-password-value",
}


@pytest.mark.parametrize("driver", ["acp", "codex_sdk", "claude_sdk"])
def test_executor_env_lacks_worker_token_and_setup_credentials(driver, tmp_path):
    with scheduler_context() as ctx:
        user_env = (
            {
                "AGENTBEACON_WORKER_TOKEN": "from-user-env",
                "AGENTBEACON_USERNAME": "from-user-env",
                "AGENTBEACON_PASSWORD": "from-user-env",
            }
            if driver != "claude_sdk"
            else None
        )
        agent_id = _seed_driver_agent(ctx["db_url"], driver, user_env)
        _, report = _report_from_driver(
            ctx, agent_id, str(tmp_path / "report.json"), worker_env=SETUP_ENV
        )
        assert report["session_token"] is not None
        assert report["worker_token"] is None
        assert report["username"] is None
        assert report["password"] is None


def _env_lines(path):
    return dict(
        line.split("=", 1)
        for line in Path(path).read_text().splitlines()
        if "=" in line
    )


@pytest.mark.parametrize("driver", ["acp", "codex_sdk", "claude_sdk"])
def test_scheduler_children_lack_setup_credentials(driver, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scheduler_bin = bin_dir / "agentbeacon"
    shutil.copy2(BASE_DIR / "bin" / "agentbeacon", scheduler_bin)
    worker_env_path = tmp_path / "worker-env"
    wrapper = bin_dir / "agentbeacon-worker"
    wrapper.write_text(
        f'#!/bin/sh\nenv > "{worker_env_path}"\nexec "{WORKER_BIN}" "$@"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    report_path = tmp_path / "report.json"
    env = {
        **SETUP_ENV,
        **EXECUTOR_ENV,
        "AGENTBEACON_MOCK_REPORT_FILE": str(report_path),
    }

    with orchestrator_context(
        max_workers=1,
        worker_poll_interval="500ms",
        env=env,
        binary=str(scheduler_bin),
    ) as orch:
        agent_id = _seed_driver_agent(orch["db_url"], driver)
        create_execution_via_api(orch["url"], agent_id, "hello")
        assert poll_until(
            lambda: report_path.exists() and report_path.stat().st_size > 0,
            timeout=40,
        )
        time.sleep(0.2)
        report = json.loads(report_path.read_text())

    worker_env = _env_lines(worker_env_path)
    assert worker_env["AGENTBEACON_MOCK_REPORT_FILE"] == str(report_path)
    assert "AGENTBEACON_WORKER_TOKEN" in worker_env
    assert "AGENTBEACON_USERNAME" not in worker_env
    assert "AGENTBEACON_PASSWORD" not in worker_env
    assert report["driver"] == driver
    assert report["session_token"] is not None
    assert report["username"] is None
    assert report["password"] is None


def _force(url, kind, status):
    resp = httpx.post(
        f"{url}/test/force_status", json={"kind": kind, "status": status}, timeout=5
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize(
    "kind", ["sync_ack", "events", "sync_turn_result", "sync_report", "final_ack"]
)
def test_worker_exits_on_auth_failure_during_session(kind, status, tmp_path):
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    url = f"http://127.0.0.1:{port}"
    scheduler = start_mock_scheduler(port, BASE_DIR)
    report_path = tmp_path / "report.json"
    forced_kind = "cancel_ack" if kind == "final_ack" else kind
    worker = None
    try:
        assert wait_for_port(port, timeout=10)
        _force(url, forced_kind, status)
        enqueue_session(url, prompt_text="STREAM_CHUNKS")
        worker = start_worker(
            url,
            interval="500ms",
            extra_env={
                **EXECUTOR_ENV,
                "AGENTBEACON_MOCK_REPORT_FILE": str(report_path),
            },
        )
        if kind == "final_ack":
            assert poll_until(
                lambda: any(
                    r.get("sessionId") == "sess-1"
                    for r in httpx.get(f"{url}/test/results", timeout=5).json()
                ),
                timeout=30,
            )
            mark_complete(url)
        assert worker.wait(timeout=30) != 0

        forced = httpx.get(f"{url}/test/forced_log", timeout=5).json()
        assert forced
        assert {(f["kind"], f["status"]) for f in forced} == {(forced_kind, status)}
        if kind == "final_ack":
            cancels = httpx.get(f"{url}/test/issued_cancels", timeout=5).json()
            assert len(cancels) == 1
            assert forced == [
                {"kind": "cancel_ack", "command_ack": cancels[0], "status": status}
            ]

        executor_pid = json.loads(report_path.read_text())["pid"]
        assert poll_until(lambda: not _alive(executor_pid), timeout=5, interval=0.1)
    finally:
        if worker is not None:
            cleanup_processes([worker])
        cleanup_processes([scheduler])
        pm.release_port(port)
