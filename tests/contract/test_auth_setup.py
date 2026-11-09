# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import (
    LOGIN_FAILED_DETAIL,
    TEST_PASSWORD,
    TEST_USERNAME,
    anonymous_headers,
    assert_unauthorized,
    db_conn,
    db_location,
    human_headers,
    scheduler_context,
)

BASE_DIR = Path(__file__).parent.parent.parent
SCHEDULER_BIN = BASE_DIR / "bin" / "agentbeacon"


def _setup(db_url, username=None, password=None, *extra, cwd=None):
    env = os.environ.copy()
    env.pop("AGENTBEACON_USERNAME", None)
    env.pop("AGENTBEACON_PASSWORD", None)
    if username is not None:
        env["AGENTBEACON_USERNAME"] = username
    if password is not None:
        env["AGENTBEACON_PASSWORD"] = password
    args = [str(SCHEDULER_BIN), "--setup-user", *extra]
    if db_url is not None:
        args += ["--db-url", db_url]
    return subprocess.run(
        args,
        env=env,
        cwd=cwd or BASE_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _login(url, password, delivery="bearer"):
    return httpx.post(
        f"{url}/api/v1/auth/login",
        json={
            "username": TEST_USERNAME,
            "password": password,
            "delivery": delivery,
        },
        headers=anonymous_headers(),
        timeout=30,
    )


def _logins(url, token):
    return httpx.get(
        f"{url}/api/v1/auth/logins",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )


def _logins_status(url, token):
    return _logins(url, token).status_code


def _users(db_url):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT username, password_hash FROM users ORDER BY username"
        ).fetchall()


def _user_login_count(db_url):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM credentials WHERE principal_type = 'user'"
        ).fetchone()[0]


@pytest.fixture
def fresh_sqlite():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    yield f"sqlite:{tmp.name}?mode=rwc"
    Path(tmp.name).unlink(missing_ok=True)


@DUAL_BACKEND
def test_setup_creates_user_from_env(test_database):
    result = _setup(test_database, "first-admin", "correct horse battery")
    assert result.returncode == 0, result.stderr
    assert f"User created in {db_location(test_database)}." in result.stdout
    users = _users(test_database)
    assert len(users) == 1
    assert users[0][0] == "first-admin"
    assert users[0][1].startswith("$argon2id$")
    assert "correct horse battery" not in users[0][1]


@DUAL_BACKEND
def test_setup_new_password_deletes_logins(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        url = ctx["url"]
        first = human_headers(url)["Authorization"].split(" ", 1)[1]
        second = _login(url, TEST_PASSWORD).json()["token"]
        assert _logins_status(url, second) == 200

        result = _setup(test_database, TEST_USERNAME, "a-brand-new-password")
        assert result.returncode == 0, result.stderr
        assert (
            f"Password changed in {db_location(test_database)}. "
            "Every existing login was signed out."
        ) in result.stdout

        assert_unauthorized(_logins(url, first))
        assert_unauthorized(_logins(url, second))
        assert _user_login_count(test_database) == 0
        assert_unauthorized(_login(url, TEST_PASSWORD), LOGIN_FAILED_DETAIL)
        assert _login(url, "a-brand-new-password").status_code == 201


@DUAL_BACKEND
def test_setup_same_password_keeps_logins(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        token = human_headers(ctx["url"])["Authorization"].split(" ", 1)[1]
        result = _setup(test_database, TEST_USERNAME, TEST_PASSWORD)
        assert result.returncode == 0, result.stderr
        assert f"User unchanged in {db_location(test_database)}." in result.stdout
        assert _logins_status(ctx["url"], token) == 200
        assert _user_login_count(test_database) == 1


def test_setup_without_credentials_or_tty_fails_when_no_user(fresh_sqlite):
    result = _setup(fresh_sqlite)
    assert result.returncode == 1
    assert (
        "setup failed: no user exists: set AGENTBEACON_USERNAME and AGENTBEACON_PASSWORD, "
        "or run --setup-user in a terminal"
    ) in result.stderr
    assert _users(fresh_sqlite) == []


@DUAL_BACKEND
def test_setup_user_without_credentials_keeps_existing_user(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        token = human_headers(ctx["url"])["Authorization"].split(" ", 1)[1]
        result = _setup(test_database)
        assert result.returncode == 0, result.stderr
        assert (
            f"User exists in {db_location(test_database)}; nothing to change."
            in result.stdout
        )
        assert _logins_status(ctx["url"], token) == 200
        assert [u[0] for u in _users(test_database)] == [TEST_USERNAME]


@DUAL_BACKEND
def test_setup_rejects_second_username(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        token = human_headers(ctx["url"])["Authorization"].split(" ", 1)[1]
        before = _users(test_database)
        result = _setup(test_database, "someone-else", "another-password")
        assert result.returncode == 1
        assert "already exists" in result.stderr
        assert _users(test_database) == before
        assert _user_login_count(test_database) == 1
        assert _logins_status(ctx["url"], token) == 200


@pytest.mark.parametrize(
    "username, password, accepted",
    [
        ("", "valid-password", False),
        ("a", "valid-password", True),
        ("u" * 64, "valid-password", True),
        ("u" * 65, "valid-password", False),
        ("has space", "valid-password", False),
        ("ok.name_with-dash", "valid-password", True),
        ("admin", "x" * 7, False),
        ("admin", "x" * 8, True),
        ("admin", "x" * 256, True),
        ("admin", "x" * 257, False),
    ],
)
def test_setup_validates_credentials(fresh_sqlite, username, password, accepted):
    result = _setup(fresh_sqlite, username, password)
    if accepted:
        assert result.returncode == 0, result.stderr
        assert [u[0] for u in _users(fresh_sqlite)] == [username]
    else:
        assert result.returncode == 1
        assert ("username" in result.stderr) or ("password" in result.stderr)
        assert _users(fresh_sqlite) == []


@DUAL_BACKEND
def test_scheduler_without_user_starts_and_refuses_login(test_database):
    with scheduler_context(db_url=test_database, create_user=False) as ctx:
        health = httpx.get(f"{ctx['url']}/api/health", timeout=5)
        assert health.status_code == 200
        resp = _login(ctx["url"], TEST_PASSWORD)
        assert_unauthorized(resp, LOGIN_FAILED_DETAIL)


def test_scheduler_without_user_logs_setup_hint():
    with scheduler_context(create_user=False) as ctx:
        log = Path(ctx["process"].log_path).read_text()
        hint = (
            f"No user exists in {db_location(ctx['db_url'])}; run `agentbeacon --setup-user` "
            "with the same --port or --db-url to create one"
        )
        assert log.count(hint) == 1
    with scheduler_context() as ctx:
        log = Path(ctx["process"].log_path).read_text()
        assert "No user exists" not in log


def test_setup_status_touches_no_database():
    with (
        tempfile.TemporaryDirectory() as workdir,
        tempfile.TemporaryDirectory() as data,
    ):
        env = os.environ.copy()
        env["AGENTBEACON_DATA_DIR"] = data
        env.pop("AGENTBEACON_USERNAME", None)
        env.pop("AGENTBEACON_PASSWORD", None)
        result = subprocess.run(
            [str(SCHEDULER_BIN), "--setup", "--status", "--port", "19999"],
            env=env,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0, result.stderr
        assert "claude:" in result.stdout
        assert list(Path(workdir).iterdir()) == []
        assert list(Path(data).glob("**/scheduler-*.db")) == []
