# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import httpx
import psycopg2
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    LOGIN_FAILED_DETAIL,
    TEST_PASSWORD,
    TEST_USERNAME,
    anonymous_headers,
    assert_problem,
    assert_unauthorized,
    db_conn,
    human_token,
    scheduler_context,
)

LOGIN_FIELDS = {"id", "label", "created_at", "expires_at"}


def _login(
    url, password=TEST_PASSWORD, delivery="bearer", label=None, username=TEST_USERNAME
):
    body = {"username": username, "password": password, "delivery": delivery}
    if label is not None:
        body["label"] = label
    return httpx.post(
        f"{url}/api/v1/auth/login", json=body, headers=anonymous_headers(), timeout=30
    )


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _logins(url, token):
    return httpx.get(f"{url}/api/v1/auth/logins", headers=_bearer(token), timeout=10)


def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _cookie_parts(set_cookie):
    pair, _, attributes = set_cookie.partition("; ")
    name, _, value = pair.partition("=")
    return name, value, attributes


@DUAL_BACKEND
def test_login_bearer_returns_token_that_authenticates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _login(ctx["url"], label="script")
        assert resp.status_code == 201
        body = resp.json()
        assert set(body) == {"token", "login"}
        assert set(body["login"]) == LOGIN_FIELDS
        listed = _logins(ctx["url"], body["token"])
        assert listed.status_code == 200
        current = [entry for entry in listed.json() if entry["current"]]
        assert [entry["id"] for entry in current] == [body["login"]["id"]]

        invalid = _logins(ctx["url"], "not-a-token")
        assert_unauthorized(invalid)


@DUAL_BACKEND
def test_login_cookie_sets_strict_httponly_cookie_and_no_token_in_body(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _login(ctx["url"], delivery="cookie", label="browser")
        assert resp.status_code == 201
        assert set(resp.json()) == {"login"}
        assert set(resp.json()["login"]) == LOGIN_FIELDS
        name, value, attributes = _cookie_parts(resp.headers["set-cookie"])
        assert name == f"agentbeacon_login_{ctx['port']}"
        assert attributes == "HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000"
        assert value not in resp.text
        with db_conn(test_database) as conn:
            row = conn.execute(
                "SELECT principal_type, label FROM credentials WHERE token_hash = ?",
                (_sha256(value),),
            ).fetchone()
        assert tuple(row) == ("user", "browser")


@DUAL_BACKEND
def test_login_wrong_password_and_unknown_user_are_identical_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        wrong = _login(ctx["url"], password="wrong-password")
        unknown = _login(ctx["url"], username="nobody")
        assert_unauthorized(wrong, LOGIN_FAILED_DETAIL)
        assert_unauthorized(unknown, LOGIN_FAILED_DETAIL)
        assert wrong.json() == unknown.json()
        assert wrong.json()["code"] == "auth.unauthorized"
        assert "invalid username or password" in wrong.json()["detail"]


@DUAL_BACKEND
def test_login_overlong_password_is_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = _login(ctx["url"], password="x" * 257)
        assert_unauthorized(resp, LOGIN_FAILED_DETAIL)
        assert resp.json() == _login(ctx["url"], password="wrong-password").json()


@pytest.mark.parametrize(
    "body, submitted",
    [
        (
            '{"username": "test-admin", "password": 123456789012345, "delivery": "bearer"}',
            "123456789012345",
        ),
        (
            '{"username": "test-admin", "password": "leaked-secret-value", "delivery": "bearer"',
            "leaked-secret-value",
        ),
        (
            '{"username": "test-admin", "password": "leaked-secret-value", "delivery": "both"}',
            "leaked-secret-value",
        ),
        (
            '{"username": "test-admin", "password": "leaked-secret-value", "extra": 98765}',
            "98765",
        ),
    ],
)
def test_malformed_login_body_is_400_without_submitted_values(body, submitted):
    with scheduler_context() as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/auth/login",
            content=body,
            headers=anonymous_headers({"Content-Type": "application/json"}),
            timeout=10,
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "code": "request.invalid",
            "detail": "the request body is not a valid login request",
            "status": 400,
            "title": "Invalid request",
        }
        assert submitted not in resp.text
        assert "test-admin" not in resp.text
        assert submitted not in json.dumps(dict(resp.headers))


@DUAL_BACKEND
def test_login_stores_only_the_token_hash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        token = _login(ctx["url"], label="hash-check").json()["token"]
        with db_conn(test_database) as conn:
            row = conn.execute(
                "SELECT principal_type, label FROM credentials WHERE token_hash = ?",
                (_sha256(token),),
            ).fetchone()
            plain = conn.execute(
                "SELECT COUNT(*) FROM credentials WHERE token_hash = ?", (token,)
            ).fetchone()[0]
        assert tuple(row) == ("user", "hash-check")
        assert plain == 0


@DUAL_BACKEND
def test_login_expires_after_default_ttl(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        login = _login(ctx["url"]).json()["login"]
        assert _parse(login["expires_at"]) - _parse(login["created_at"]) == timedelta(
            days=30
        )


def test_login_ttl_is_configurable():
    with scheduler_context(env={"AGENTBEACON_LOGIN_TTL": "2h"}) as ctx:
        resp = _login(ctx["url"], delivery="cookie")
        login = resp.json()["login"]
        assert _parse(login["expires_at"]) - _parse(login["created_at"]) == timedelta(
            hours=2
        )
        assert "Max-Age=7200" in resp.headers["set-cookie"]


@DUAL_BACKEND
def test_expired_login_is_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        token = _login(ctx["url"]).json()["token"]
        past = (
            "CURRENT_TIMESTAMP - INTERVAL '1 hour'"
            if test_database.startswith("postgres")
            else "datetime('now', '-1 hour')"
        )
        with db_conn(test_database) as conn:
            conn.execute(
                f"UPDATE credentials SET expires_at = {past} WHERE token_hash = ?",
                (_sha256(token),),
            )
            conn.commit()
        resp = _logins(ctx["url"], token)
        assert_unauthorized(resp)


@DUAL_BACKEND
def test_list_logins_shows_own_logins(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        first = _login(ctx["url"], label="first").json()
        second = _login(ctx["url"], label="second").json()
        listed = _logins(ctx["url"], second["token"]).json()
        assert all(set(entry) == LOGIN_FIELDS | {"current"} for entry in listed)
        assert sorted(
            (entry["id"], entry["label"], entry["current"]) for entry in listed
        ) == sorted(
            [
                (_sha256(human_token(ctx["url"])), "pytest", False),
                (_sha256(first["token"]), "first", False),
                (_sha256(second["token"]), "second", True),
            ]
        )
        assert first["login"]["id"] == _sha256(first["token"])
        assert second["login"]["id"] == _sha256(second["token"])


@DUAL_BACKEND
def test_delete_login_revokes_only_that_login(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        keep = _login(ctx["url"]).json()
        drop = _login(ctx["url"]).json()
        resp = httpx.delete(
            f"{ctx['url']}/api/v1/auth/logins/{drop['login']['id']}",
            headers=_bearer(keep["token"]),
            timeout=10,
        )
        assert resp.status_code == 204
        assert_unauthorized(_logins(ctx["url"], drop["token"]))
        assert _logins(ctx["url"], keep["token"]).status_code == 200


@DUAL_BACKEND
def test_delete_unknown_login_is_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/v1/auth/logins/{'0' * 64}", timeout=10)
        assert_problem(resp, 404, "resource.not_found", "no login has this id")


@DUAL_BACKEND
def test_logout_deletes_current_login_and_clears_cookie(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        cookie_resp = _login(url, delivery="cookie")
        name, value, _ = _cookie_parts(cookie_resp.headers["set-cookie"])
        resp = httpx.post(
            f"{url}/api/v1/auth/logout",
            headers=anonymous_headers({"Cookie": f"{name}={value}", "Origin": url}),
            timeout=10,
        )
        assert resp.status_code == 204
        assert resp.headers["set-cookie"] == (
            f"{name}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        )
        with db_conn(test_database) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM credentials WHERE token_hash = ?",
                (_sha256(value),),
            ).fetchone()[0]
        assert count == 0

        token = _login(url).json()["token"]
        bearer_logout = httpx.post(
            f"{url}/api/v1/auth/logout", headers=_bearer(token), timeout=10
        )
        assert bearer_logout.status_code == 204
        assert "set-cookie" not in bearer_logout.headers
        assert_unauthorized(_logins(url, token))


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
    raise AssertionError("the login never waited on the user row")


@DUAL_BACKEND
def test_login_racing_password_change_leaves_no_old_password_login(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        result = {}

        def login():
            result["response"] = _login(ctx["url"])

        if test_database.startswith("postgres"):
            holder = psycopg2.connect(test_database)
            cur = holder.cursor()
            cur.execute("SELECT id FROM users FOR UPDATE")
            cur.execute("UPDATE users SET password_hash = 'replaced-hash'")
            cur.execute("DELETE FROM credentials WHERE principal_type = 'user'")
            thread = threading.Thread(target=login)
            thread.start()
            _wait_for_pg_lock_waiter(test_database)
            holder.commit()
            holder.close()
        else:
            path = test_database.split("sqlite:")[1].split("?")[0]
            holder = sqlite3.connect(path, isolation_level=None)
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("UPDATE users SET password_hash = 'replaced-hash'")
            holder.execute("DELETE FROM credentials WHERE principal_type = 'user'")
            thread = threading.Thread(target=login)
            thread.start()
            time.sleep(2)
            holder.execute("COMMIT")
            holder.close()

        thread.join(timeout=30)
        assert not thread.is_alive()
        resp = result["response"]
        assert_unauthorized(resp, LOGIN_FAILED_DETAIL)
        with db_conn(test_database) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM credentials WHERE principal_type = 'user'"
            ).fetchone()[0]
        assert count == 0
