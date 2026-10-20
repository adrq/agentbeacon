# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import re
import time
from pathlib import Path

import httpx
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.worker_test_helpers import enqueue_session, poll_until
from tests.mock_agent_helpers import (
    insert_task,
    start_mock_scheduler,
    make_executor_report,
    post_worker_sync,
    set_execution_fields,
    set_session_fields,
)
from tests.testhelpers import (
    agent_headers,
    anonymous_headers,
    assert_unauthorized,
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    PortManager,
    scheduler_context,
    seed_test_agent,
    start_worker,
    wait_for_port,
    wid,
)

V1 = "/api/v1"
BASE_DIR = Path(__file__).parent.parent.parent


def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _bearer(token):
    return anonymous_headers({"Authorization": f"Bearer {token}"})


def _read(url, exec_id, token):
    return httpx.get(
        f"{url}{V1}/executions/{exec_id}", headers=_bearer(token), timeout=10
    )


def _read_status(url, exec_id, token):
    return _read(url, exec_id, token).status_code


def _session_hash(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT token_hash FROM credentials "
            "WHERE principal_type = 'session' AND principal_id = ?",
            (session_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _assign(url, worker):
    response = post_worker_sync(url, worker)
    assert response["type"] == "command", response
    assert response["action"]["type"] == "assign", response
    return response


def _start(ctx, worker):
    response = _assign(ctx["url"], worker)
    session_id = response["action"]["session_id"]
    post_worker_sync(
        ctx["url"],
        worker,
        executor_report=make_executor_report(session_id, "running"),
        command_ack=response["token"],
    )
    post_worker_sync(
        ctx["url"], worker, executor_report=make_executor_report(session_id, "idle")
    )
    return response


@DUAL_BACKEND
def test_assign_carries_session_token_and_only_its_hash_is_stored(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-assign")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        response = _assign(ctx["url"], wid("w1"))
        token = response["action"]["session_token"]
        assert response["action"]["session_id"] == session_id
        assert _session_hash(test_database, session_id) == [_sha256(token)]
        assert _read_status(ctx["url"], exec_id, token) == 200


@DUAL_BACKEND
def test_token_replaced_on_each_assign(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-reassign")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        first = _start(ctx, wid("w1"))["action"]["session_token"]
        set_session_fields(
            test_database,
            session_id,
            executor_state="unassigned",
            worker_id=None,
            command_token=None,
            command_type=None,
        )
        resp = httpx.post(
            f"{ctx['url']}{V1}/sessions/{session_id}/message",
            json={"parts": [{"text": "again"}]},
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        second = _assign(ctx["url"], wid("w2"))["action"]["session_token"]
        assert second != first
        assert _session_hash(test_database, session_id) == [_sha256(second)]
        assert_unauthorized(_read(ctx["url"], exec_id, first))
        assert _read_status(ctx["url"], exec_id, second) == 200


@DUAL_BACKEND
@pytest.mark.parametrize("queued", [True, False])
def test_token_replaced_on_recovery_resume_assign(test_database, queued):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-resume")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        first = _start(ctx, wid("w1"))["action"]["session_token"]
        assert _read_status(ctx["url"], exec_id, first) == 200
        with db_conn(test_database) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()
        set_session_fields(
            test_database,
            session_id,
            executor_state="crashed",
            agent_session_id="agent-session-resume",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
            worker_id=None,
            command_token=None,
            command_type=None,
        )
        if queued:
            insert_task(test_database, exec_id, session_id, "queued work")

        for _ in range(20):
            post_worker_sync(ctx["url"], wid("tick"))
            with db_conn(test_database) as conn:
                state = conn.execute(
                    "SELECT executor_state FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()[0]
            if state == "unassigned":
                break
            time.sleep(0.25)
        assert state == "unassigned"

        assign = _assign(ctx["url"], wid("w2"))["action"]
        assert assign["session_id"] == session_id
        assert assign["resume"] is True
        assert (assign["payload"] is not None) is queued
        second = assign["session_token"]
        assert second != first
        assert _session_hash(test_database, session_id) == [_sha256(second)]
        assert_unauthorized(_read(ctx["url"], exec_id, first))
        assert _read_status(ctx["url"], exec_id, second) == 200


def _command_token(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT command_token FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]


def _feed_and_accept(ctx, worker, session_id):
    resp = httpx.post(
        f"{ctx['url']}{V1}/sessions/{session_id}/message",
        json={"parts": [{"text": "more"}]},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    feed = post_worker_sync(
        ctx["url"], worker, executor_report=make_executor_report(session_id, "idle")
    )
    assert feed["action"]["type"] == "feed_turn", feed
    post_worker_sync(
        ctx["url"],
        worker,
        executor_report=make_executor_report(session_id, "running"),
        command_ack=feed["token"],
    )
    assert _command_token(ctx["db_url"], session_id) is None
    return feed


@DUAL_BACKEND
def test_token_kept_across_feed_turn_stop_turn_and_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-keep")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        worker = wid("w1")
        token = _start(ctx, worker)["action"]["session_token"]
        stored = _session_hash(test_database, session_id)
        assert stored == [_sha256(token)]

        feed = _feed_and_accept(ctx, worker, session_id)
        assert "session_token" not in feed["action"]
        assert _session_hash(test_database, session_id) == stored
        assert _read_status(ctx["url"], exec_id, token) == 200

        stop = httpx.post(f"{ctx['url']}{V1}/sessions/{session_id}/stop", timeout=10)
        assert stop.status_code == 200, stop.text
        stop_turn = post_worker_sync(ctx["url"], worker)
        assert stop_turn["action"]["type"] == "stop_turn", stop_turn
        assert "session_token" not in stop_turn["action"]
        post_worker_sync(
            ctx["url"],
            worker,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=stop_turn["token"],
        )
        assert _command_token(test_database, session_id) is None
        assert _session_hash(test_database, session_id) == stored
        assert _read_status(ctx["url"], exec_id, token) == 200

        _feed_and_accept(ctx, worker, session_id)
        terminate = httpx.post(
            f"{ctx['url']}{V1}/executions/{exec_id}/terminate", timeout=10
        )
        assert terminate.status_code == 200, terminate.text
        cancel = post_worker_sync(ctx["url"], worker)
        assert cancel["action"]["type"] == "cancel", cancel
        assert "session_token" not in cancel["action"]
        post_worker_sync(
            ctx["url"],
            worker,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=cancel["token"],
        )
        assert _command_token(test_database, session_id) is None
        assert _session_hash(test_database, session_id) == stored


@DUAL_BACKEND
def test_token_rejected_once_session_ends_and_row_remains(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-ended")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        token = _start(ctx, wid("w1"))["action"]["session_token"]
        resp = httpx.post(
            f"{ctx['url']}{V1}/executions/{exec_id}/terminate", timeout=10
        )
        assert resp.status_code == 200, resp.text
        assert_unauthorized(_read(ctx["url"], exec_id, token))
        mcp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers=_bearer(token),
            timeout=10,
        )
        assert_unauthorized(mcp)
        assert _session_hash(test_database, session_id) == [_sha256(token)]


@DUAL_BACKEND
def test_token_rejected_while_execution_terminating(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-terminating")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        token = _start(ctx, wid("w1"))["action"]["session_token"]
        set_execution_fields(test_database, exec_id, desired="terminate", outcome=None)
        assert_unauthorized(_read(ctx["url"], exec_id, token))


@DUAL_BACKEND
def test_token_validity_ignores_ancestor_state(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-ancestor")
        exec_id, root = create_execution_via_api(ctx["url"], agent_id, "task")
        result = mcp_tools_call(
            ctx["url"], root, "delegate", {"agent": "token-ancestor", "prompt": "child"}
        )
        child = json.loads(result["content"][0]["text"])["session_id"]
        child_headers = agent_headers(ctx["url"], child)
        set_session_fields(test_database, root, desired="terminate", outcome=None)
        resp = httpx.get(
            f"{ctx['url']}{V1}/executions/{exec_id}", headers=child_headers, timeout=10
        )
        assert resp.status_code == 200, resp.text


@DUAL_BACKEND
def test_mcp_session_id_header_must_match_the_principal(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-mcp-sid")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _, other = create_execution_via_api(ctx["url"], agent_id, "other")
        headers = agent_headers(
            ctx["url"],
            session_id,
            {"Accept": "application/json, text/event-stream"},
        )
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        same = httpx.post(
            f"{ctx['url']}/mcp",
            json=body,
            headers={**headers, "Mcp-Session-Id": session_id},
            timeout=10,
        )
        assert same.status_code == 200, same.text
        other_resp = httpx.post(
            f"{ctx['url']}/mcp",
            json=body,
            headers={**headers, "Mcp-Session-Id": other},
            timeout=10,
        )
        assert other_resp.status_code == 400
        empty = httpx.post(
            f"{ctx['url']}/mcp",
            json=body,
            headers={**headers, "Mcp-Session-Id": ""},
            timeout=10,
        )
        assert empty.status_code == 400


def test_api_docs_use_session_token():
    with scheduler_context() as ctx:
        docs = httpx.get(f"{ctx['url']}/api/docs", timeout=10).text
        assert "Bearer $AGENTBEACON_SESSION_TOKEN" in docs
        assert "Bearer $AGENTBEACON_SESSION_ID" not in docs


PUBLIC_PATHS = ("/api/docs", "/api/health", "/api/ready", "/api/versions")


def _curl_commands(text):
    commands, current = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("curl"):
            current = [stripped]
            commands.append(current)
        elif current is not None and stripped.startswith("-"):
            current.append(stripped)
        else:
            current = None
    return [" ".join(c) for c in commands]


def _assert_protected_examples_authenticate(text):
    commands = _curl_commands(text)
    assert commands
    for command in commands:
        path = re.search(r"\$AGENTBEACON_API_BASE(/[^\"\s?]*)", command).group(1)
        if path in PUBLIC_PATHS:
            continue
        assert "Authorization: Bearer $AGENTBEACON_SESSION_TOKEN" in command, command


def test_every_protected_briefing_example_authenticates():
    with scheduler_context() as ctx:
        lead_agent = seed_test_agent(ctx["db_url"], name="brief-lead")
        leaf_agent = seed_test_agent(ctx["db_url"], name="brief-leaf")
        exec_id, root = create_execution_via_api(ctx["url"], lead_agent, "coordinate")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("UPDATE executions SET max_depth = 2 WHERE id = ?", (exec_id,))
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, leaf_agent),
            )
            conn.commit()
        prompts = [
            _assign(ctx["url"], wid("w-root"))["action"]["agent_config"][
                "system_prompt"
            ]
        ]
        sub = mcp_tools_call(
            ctx["url"], root, "delegate", {"agent": "brief-leaf", "prompt": "a"}
        )
        sub_id = json.loads(sub["content"][0]["text"])["session_id"]
        prompts.append(
            _assign(ctx["url"], wid("w-sub"))["action"]["agent_config"]["system_prompt"]
        )
        mcp_tools_call(
            ctx["url"], sub_id, "delegate", {"agent": "brief-leaf", "prompt": "b"}
        )
        prompts.append(
            _assign(ctx["url"], wid("w-leaf"))["action"]["agent_config"][
                "system_prompt"
            ]
        )
        for prompt in prompts:
            assert "Bearer $AGENTBEACON_SESSION_ID" not in prompt
            _assert_protected_examples_authenticate(prompt)
        _assert_protected_examples_authenticate(
            httpx.get(f"{ctx['url']}/api/docs", timeout=10).text
        )


class _ExecutionActivityUpdateFails:
    def __init__(self, db_url):
        self.db_url = db_url

    def __enter__(self):
        with db_conn(self.db_url) as conn:
            if self.db_url.startswith("postgres"):
                conn.execute(
                    "CREATE OR REPLACE FUNCTION failpoint_activity() RETURNS trigger AS $$ "
                    "BEGIN RAISE EXCEPTION 'failpoint'; END; $$ LANGUAGE plpgsql"
                )
                conn.execute(
                    "CREATE TRIGGER failpoint_activity_trigger BEFORE UPDATE OF "
                    "last_activity_at ON executions FOR EACH ROW "
                    "EXECUTE FUNCTION failpoint_activity()"
                )
            else:
                conn.execute(
                    "CREATE TRIGGER failpoint_activity_trigger BEFORE UPDATE OF "
                    "last_activity_at ON executions "
                    "BEGIN SELECT RAISE(ABORT, 'failpoint'); END"
                )
            conn.commit()
        return self

    def __exit__(self, *_exc):
        with db_conn(self.db_url) as conn:
            if self.db_url.startswith("postgres"):
                conn.execute(
                    "DROP TRIGGER IF EXISTS failpoint_activity_trigger ON executions"
                )
                conn.execute("DROP FUNCTION IF EXISTS failpoint_activity()")
            else:
                conn.execute("DROP TRIGGER IF EXISTS failpoint_activity_trigger")
            conn.commit()


@DUAL_BACKEND
def test_failed_assign_keeps_the_previous_session_credential(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="token-failed-assign")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        agent_headers(ctx["url"], session_id)
        previous = _session_hash(test_database, session_id)
        worker = wid("w1")

        def snapshot():
            with db_conn(test_database) as conn:
                row = conn.execute(
                    "SELECT executor_state, worker_id, command_token, command_type, "
                    "command_at, command_has_payload FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                queued = conn.execute(
                    "SELECT id, task_payload, source FROM task_queue "
                    "WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
            return tuple(row), [tuple(q) for q in queued]

        before = snapshot()
        assert before[0][1:5] == (None, None, None, None)
        assert len(before[1]) == 1

        with _ExecutionActivityUpdateFails(test_database):
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": worker},
                timeout=30,
            )

        assert _session_hash(test_database, session_id) == previous
        assert snapshot() == before

        response = _assign(ctx["url"], worker)
        assert _session_hash(test_database, session_id) == [
            _sha256(response["action"]["session_token"])
        ]
        assert _session_hash(test_database, session_id) != previous


KNOWN_TOKEN = "known-session-token-value-0123456789"


@pytest.mark.parametrize(
    "action",
    [
        {
            "type": "assign",
            "session_id": "sess-2",
            "resume": "not-a-boolean",
            "session_token": KNOWN_TOKEN,
        },
        {"type": KNOWN_TOKEN, "session_id": "sess-2"},
    ],
    ids=["invalid_field", "unknown_variant"],
)
def test_worker_debug_log_omits_session_token(action):
    known = KNOWN_TOKEN
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    url = f"http://127.0.0.1:{port}"
    scheduler = start_mock_scheduler(port, BASE_DIR)
    worker = None
    try:
        assert wait_for_port(port, timeout=10)
        httpx.post(
            f"{url}/test/force_status",
            json={"kind": "events", "status": 500, "count": 1},
            timeout=5,
        )
        enqueue_session(url, prompt_text="STREAM_CHUNKS")
        worker = start_worker(
            url,
            extra_env={"RUST_LOG": "agentbeacon_worker::sync=debug"},
        )
        assert poll_until(
            lambda: any(
                r.get("sessionId") == "sess-1"
                for r in httpx.get(f"{url}/test/results", timeout=5).json()
            ),
            timeout=30,
        )
        httpx.post(
            f"{url}/test/raw_sync_response",
            json={"type": "command", "token": "cmd-raw", "action": action},
            timeout=5,
        )
        assert poll_until(
            lambda: not httpx.get(f"{url}/test/raw_sync_queue", timeout=5).json(),
            timeout=30,
        )
        time.sleep(1)
    finally:
        if worker is not None:
            cleanup_processes([worker])
        cleanup_processes([scheduler])
        pm.release_port(port)
    output = re.sub(r"\x1b\[[0-9;]*m", "", worker.stdout.read())
    assert "worker event POST failed, retrying" in output
    assert "failed to parse sync response" in output
    assert "category=Data" in output
    assert known not in output
