# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import os
import pathlib
import subprocess
import threading
import time

import httpx
import pytest

from tests.wiki_helpers import build_world, materialize_page, wiki_patch
from tests.testhelpers import (
    TEST_PASSWORD,
    TEST_USERNAME,
    agent_headers,
    anonymous_headers,
    assert_forbidden,
    create_execution_via_api,
    db_conn,
    human_headers,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)

V1 = "/api/v1"


def _cookie_login(url):
    resp = httpx.post(
        f"{url}{V1}/auth/login",
        json={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "delivery": "cookie",
        },
        headers=anonymous_headers(),
        timeout=30,
    )
    assert resp.status_code == 201, resp.text
    return resp.headers["set-cookie"].split(";", 1)[0]


def _open_stream(url, exec_id, headers, frames, stop):
    with httpx.stream(
        "GET",
        f"{url}{V1}/executions/{exec_id}/events/stream",
        headers=headers,
        timeout=httpx.Timeout(30, read=60),
    ) as resp:
        frames.append(("status", resp.status_code))
        try:
            for line in resp.iter_lines():
                if stop.is_set():
                    return
                frames.append(("line", line))
        except httpx.RemoteProtocolError:
            pass
        frames.append(("closed", None))


def _message_lines(frames, marker):
    return [f for f in frames if f[0] == "line" and marker in (f[1] or "")]


def _post_user_message(url, session_id, text):
    resp = httpx.post(
        f"{url}{V1}/sessions/{session_id}/message",
        json={"parts": [{"text": text}]},
        headers=human_headers(url),
        timeout=10,
    )
    assert resp.status_code == 200, resp.text


def _revoke_by_delete(ctx, cookie):
    token = cookie.split("=", 1)[1]
    resp = httpx.delete(
        f"{ctx['url']}{V1}/auth/logins/{hashlib.sha256(token.encode()).hexdigest()}",
        headers=human_headers(ctx["url"]),
        timeout=10,
    )
    assert resp.status_code == 204


def test_agent_messages_any_session_in_its_execution():
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="msg-anyone")
        exec_id, root = create_execution_via_api(ctx["url"], agent_id, "task")
        result = mcp_tools_call(
            ctx["url"], root, "delegate", {"agent": "msg-anyone", "prompt": "a"}
        )
        child_a = json.loads(result["content"][0]["text"])["session_id"]
        result = mcp_tools_call(
            ctx["url"], root, "delegate", {"agent": "msg-anyone", "prompt": "b"}
        )
        child_b = json.loads(result["content"][0]["text"])["session_id"]
        result = mcp_tools_call(
            ctx["url"], child_a, "delegate", {"agent": "msg-anyone", "prompt": "c"}
        )
        grandchild = json.loads(result["content"][0]["text"])["session_id"]
        names = {
            s["session_id"]: s["hierarchical_name"]
            for s in httpx.get(
                f"{ctx['url']}{V1}/executions/{exec_id}/sessions", timeout=10
            ).json()
        }
        for target in (root, child_a, grandchild):
            resp = httpx.post(
                f"{ctx['url']}{V1}/messages",
                json={"to": names[target], "parts": [{"text": "hello"}]},
                headers=agent_headers(ctx["url"], child_b),
                timeout=10,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["recipient_session_id"] == target


@pytest.mark.parametrize(
    "cause", ["deleted", "expired", "password_changed", "agent_rotated"]
)
def test_open_sse_stream_ends_when_credential_invalid(cause):
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-revoke")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        if cause == "agent_rotated":
            stream_headers = agent_headers(ctx["url"], session_id)
        else:
            cookie = _cookie_login(ctx["url"])
            stream_headers = anonymous_headers({"Cookie": cookie})
        frames, stop = [], threading.Event()
        thread = threading.Thread(
            target=_open_stream,
            args=(ctx["url"], exec_id, stream_headers, frames, stop),
            daemon=True,
        )
        thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not _message_lines(frames, "event: position"):
            time.sleep(0.05)
        _post_user_message(ctx["url"], session_id, "before-marker")
        deadline = time.time() + 10
        while time.time() < deadline and not _message_lines(frames, "before-marker"):
            time.sleep(0.1)
        assert _message_lines(frames, "before-marker")

        if cause == "deleted":
            _revoke_by_delete(ctx, cookie)
        elif cause == "expired":
            token = cookie.split("=", 1)[1]
            past = (
                "CURRENT_TIMESTAMP - INTERVAL '1 hour'"
                if ctx["db_url"].startswith("postgres")
                else "datetime('now', '-1 hour')"
            )
            with db_conn(ctx["db_url"]) as conn:
                conn.execute(
                    f"UPDATE credentials SET expires_at = {past} WHERE token_hash = ?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                )
                conn.commit()
        elif cause == "password_changed":
            env = {**os.environ, "AGENTBEACON_USERNAME": TEST_USERNAME}
            env["AGENTBEACON_PASSWORD"] = "a-different-password"
            result = subprocess.run(
                [
                    "./bin/agentbeacon",
                    "--setup",
                    "--user-only",
                    "--db-url",
                    ctx["db_url"],
                ],
                cwd=pathlib.Path(__file__).resolve().parents[2],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
            assert result.returncode == 0, result.stderr
            ctx_token = login_after_password_change(ctx["url"], "a-different-password")
            human = {"Authorization": f"Bearer {ctx_token}"}
        else:
            with db_conn(ctx["db_url"]) as conn:
                conn.execute(
                    "UPDATE credentials SET token_hash = ? "
                    "WHERE principal_type = 'session' AND principal_id = ?",
                    (hashlib.sha256(b"next-assign").hexdigest(), session_id),
                )
                conn.commit()

        if cause == "password_changed":
            resp = httpx.post(
                f"{ctx['url']}{V1}/sessions/{session_id}/message",
                json={"parts": [{"text": "after-marker"}]},
                headers=human,
                timeout=10,
            )
            assert resp.status_code == 200, resp.text
        else:
            _post_user_message(ctx["url"], session_id, "after-marker")
        thread.join(timeout=20)
        stop.set()
        assert not thread.is_alive()
        assert ("closed", None) in frames
        assert _message_lines(frames, "after-marker") == []


def login_after_password_change(url, password):
    resp = httpx.post(
        f"{url}{V1}/auth/login",
        json={"username": TEST_USERNAME, "password": password, "delivery": "bearer"},
        headers=anonymous_headers(),
        timeout=30,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _backlog(db_url, exec_id, session_id, count):
    with db_conn(db_url) as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (
                    exec_id,
                    session_id,
                    json.dumps(
                        {
                            "role": "ROLE_AGENT",
                            "parts": [{"text": f"backlog-{i} " + "x" * 2000}],
                        }
                    ),
                ),
            )
        conn.commit()


def test_sse_stream_revoked_while_draining_a_backlog_stops_early():
    total = 3000
    with scheduler_context() as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-backlog")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        cookie = _cookie_login(ctx["url"])
        received = 0
        with httpx.stream(
            "GET",
            f"{ctx['url']}{V1}/executions/{exec_id}/events/stream",
            headers=anonymous_headers({"Cookie": cookie}),
            timeout=httpx.Timeout(30, read=60),
        ) as resp:
            assert resp.status_code == 200
            lines = resp.iter_lines()
            for line in lines:
                if line.startswith("event: position"):
                    break
            _backlog(ctx["db_url"], exec_id, session_id, total)
            _post_user_message(ctx["url"], session_id, "wake")
            for line in lines:
                if line.startswith("data:") and "backlog-" in line:
                    received += 1
                    break
            assert received == 1
            _revoke_by_delete(ctx, cookie)
            for line in lines:
                if line.startswith("data:") and "backlog-" in line:
                    received += 1
        assert received < total


def test_wiki_sharing_denials_keep_their_error_body():
    with scheduler_context() as ctx:
        projects, sessions, _ = build_world(ctx)
        owner = projects["owner"]["id"]
        materialize_page(ctx["url"], owner, "shared-page", tagged=True)
        resp = wiki_patch(
            ctx["url"],
            owner,
            "shared-page",
            2,
            edits=[{"old_string": "matrix body text", "new_string": "edited"}],
            session_id=sessions["read_member"],
        )
        assert resp.status_code == 403
        assert not resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json() == {"error": "read_only_member"}


def _cookie_project_post(url, cookie, origin):
    return httpx.post(
        f"{url}{V1}/projects",
        json={"name": f"csrf-{os.urandom(4).hex()}", "path": "/tmp"},
        headers=anonymous_headers({"Cookie": cookie, "Origin": origin}),
        timeout=10,
    )


def test_cookie_write_origin_follows_the_cors_allow_list():
    env = {"CORS_ALLOWED_ORIGINS": "https://allowed.example"}
    with scheduler_context(env=env) as ctx:
        cookie = _cookie_login(ctx["url"])
        allowed = _cookie_project_post(ctx["url"], cookie, "https://allowed.example")
        assert allowed.status_code == 201, allowed.text
        assert_forbidden(
            _cookie_project_post(ctx["url"], cookie, "https://unlisted.example")
        )
