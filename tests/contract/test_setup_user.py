# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

import httpx
import pytest

from tests.testhelpers import (
    LOGIN_FAILED_DETAIL,
    anonymous_headers,
    assert_unauthorized,
    run_pty,
    scheduler_context,
)

BASE_DIR = Path(__file__).parent.parent.parent
SCHEDULER_BIN = BASE_DIR / "bin" / "agentbeacon"
PASSWORD = "setup-user-password"
SENTINEL = "pw-sentinel-91"

DEFAULT_WARNING = (
    "warning: no database URL given; using SQLite at {path}. To use a different database, "
    "pass --db-url or set DATABASE_URL, here and when starting AgentBeacon."
)
REFUSAL = "No database was created. Pass --db-url or set DATABASE_URL to use a different database."
EXPLICIT_REMINDER = "Start AgentBeacon with the same --db-url or DATABASE_URL, or it will not find this user."
CREATE_PROMPT = "Create it?"
UNSUPPORTED = (
    "unsupported SQLite URL '{url}': use sqlite:<path>, "
    "optionally followed by ?mode=rwc, ?mode=rw or ?mode=ro"
)


def _env(home, credentials=True, **extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PG")
        and k
        not in (
            "DATABASE_URL",
            "AGENTBEACON_WIKI_INDEX_DIR",
            "AGENTBEACON_PORT",
            "AGENTBEACON_USERNAME",
            "AGENTBEACON_PASSWORD",
        )
    }
    env["HOME"] = str(home / "home")
    env["AGENTBEACON_DATA_DIR"] = str(home / "data")
    if credentials:
        env["AGENTBEACON_USERNAME"] = "setup-admin"
        env["AGENTBEACON_PASSWORD"] = PASSWORD
    env.update(extra)
    return env


def _run(args, cwd, env, timeout=90):
    return subprocess.run(
        [str(SCHEDULER_BIN), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _run_pty(args, cwd, env, replies=(), timeout=90):
    return run_pty([str(SCHEDULER_BIN), *args], cwd, env, replies, timeout)


def _dirs(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (tmp_path / "home").mkdir()
    return cwd


def _users(path):
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute("SELECT username FROM users")]


def test_setup_installs_sdks_without_database(tmp_path):
    cwd = _dirs(tmp_path)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    log = tmp_path / "npm.log"
    npm = stub_dir / "npm"
    npm.write_text(f'#!/bin/sh\nprintf \'%s\\t%s\\n\' "$PWD" "$*" >> "{log}"\nexit 0\n')
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    env = _env(tmp_path, credentials=False)
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    result = _run(["--setup", "--db-url", "mysql://x"], cwd, env)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        f"{cwd}\t--version",
        f"{tmp_path / 'data'}\tci --omit=dev",
    ]
    assert "User" not in result.stdout
    assert list(cwd.iterdir()) == []


def test_setup_and_setup_user_are_mutually_exclusive(tmp_path):
    cwd = _dirs(tmp_path)
    result = _run(["--setup", "--setup-user"], cwd, _env(tmp_path))
    assert result.returncode == 2
    assert "the argument '--setup' cannot be used with '--setup-user'" in result.stderr


def test_setup_user_default_is_working_directory(tmp_path):
    cwd = _dirs(tmp_path)
    result = _run(["--setup-user"], cwd, _env(tmp_path))
    assert result.returncode == 0, result.stderr
    path = cwd / "scheduler-9456.db"
    assert result.stdout.splitlines()[0] == f"User database: SQLite at {path}"
    assert DEFAULT_WARNING.format(path=path) in result.stderr.splitlines()
    assert path.is_file()
    assert list((tmp_path / "home").iterdir()) == []
    assert not (tmp_path / "data").exists()


def test_setup_user_explicit_sqlite_flag_names_file_without_warning(tmp_path):
    cwd = _dirs(tmp_path)
    result = _run(
        ["--setup-user", "--db-url", "sqlite:x.db?mode=rwc"], cwd, _env(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"User database: SQLite at {cwd / 'x.db'}"
    assert lines[-1] == EXPLICIT_REMINDER
    assert "no database URL given" not in result.stderr


def test_setup_user_database_url_env_does_not_warn(tmp_path):
    cwd = _dirs(tmp_path)
    env = _env(tmp_path, DATABASE_URL="sqlite:x.db?mode=rwc")
    result = _run(["--setup-user"], cwd, env)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"User database: SQLite at {cwd / 'x.db'}"
    assert lines[-1] == EXPLICIT_REMINDER
    assert "no database URL given" not in result.stderr


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            f"postgres://u:{SENTINEL}@127.0.0.1:1/db?sslmode=disable",
            'PostgreSQL database "db" on 127.0.0.1:1 as u',
        ),
        (
            f"postgres://alice:{SENTINEL}@127.0.0.1:1?sslmode=disable",
            'PostgreSQL database "alice" on 127.0.0.1:1 as alice',
        ),
        (
            f"postgres://u:{SENTINEL}@localhost/app?host={{socket}}",
            'PostgreSQL database "app" on socket {socket} (port 5432) as u',
        ),
    ],
    ids=["tcp", "database-defaults-to-user", "socket-in-url"],
)
def test_setup_user_postgres_location_line_hides_password(tmp_path, url, expected):
    cwd = _dirs(tmp_path)
    socket = tmp_path / "socket"
    socket.mkdir()
    result = _run(
        ["--setup-user", "--db-url", url.format(socket=socket)], cwd, _env(tmp_path)
    )
    assert result.returncode == 1
    assert (
        result.stdout.splitlines()[0]
        == f"User database: {expected.format(socket=socket)}"
    )
    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr


def test_start_logs_postgres_location_without_password(tmp_path):
    cwd = _dirs(tmp_path)
    url = f"postgres://u:{SENTINEL}@127.0.0.1:1/db?sslmode=disable"
    result = _run(
        [
            "--port",
            "0",
            "--max-workers",
            "0",
            "--db-url",
            url,
            "--wiki-index-dir",
            "index",
        ],
        cwd,
        _env(tmp_path, credentials=False),
    )
    assert result.returncode == 1
    messages = []
    for line in result.stdout.splitlines():
        try:
            messages.append(json.loads(line)["fields"]["message"])
        except (ValueError, KeyError):
            continue
    assert (
        'Connecting to database: PostgreSQL database "db" on 127.0.0.1:1 as u'
        in messages
    )
    assert SENTINEL not in result.stdout
    assert SENTINEL not in result.stderr


def test_setup_user_postgres_socket_from_pghost_is_named(tmp_path):
    cwd = _dirs(tmp_path)
    socket = tmp_path / "socket"
    socket.mkdir()
    env = _env(tmp_path, PGHOST=str(socket))
    result = _run(["--setup-user", "--db-url", "postgres:///app?user=u"], cwd, env)
    assert result.returncode == 1
    assert (
        result.stdout.splitlines()[0]
        == f'User database: PostgreSQL database "app" on socket {socket} (port 5432) as u'
    )


def test_setup_user_non_default_port_reminds_port(tmp_path):
    cwd = _dirs(tmp_path)
    result = _run(["--setup-user", "--port", "19458"], cwd, _env(tmp_path))
    assert result.returncode == 0, result.stderr
    reminder = "Start AgentBeacon with --port 19458 (or AGENTBEACON_PORT=19458) to use this database."
    assert result.stdout.splitlines().count(reminder) == 1


def test_setup_user_interactive_decline_creates_nothing(tmp_path):
    cwd = _dirs(tmp_path)
    code, output, stderr = _run_pty(
        ["--setup-user"], cwd, _env(tmp_path), [(CREATE_PROMPT, "n\n")]
    )
    assert code == 1
    assert CREATE_PROMPT in output
    assert stderr.splitlines()[-1] == REFUSAL
    assert list(cwd.iterdir()) == []


def test_setup_user_interactive_default_answer_creates_database(tmp_path):
    cwd = _dirs(tmp_path)
    code, output, stderr = _run_pty(
        ["--setup-user"], cwd, _env(tmp_path), [(CREATE_PROMPT, "\n")]
    )
    path = cwd / "scheduler-9456.db"
    assert code == 0, stderr
    assert f"User created in SQLite at {path}." in output
    assert path.is_file()


def test_setup_user_interactive_existing_database_is_not_confirmed(tmp_path):
    cwd = _dirs(tmp_path)
    assert _run(["--setup-user"], cwd, _env(tmp_path)).returncode == 0
    code, output, stderr = _run_pty(["--setup-user"], cwd, _env(tmp_path))
    assert code == 0, stderr
    assert CREATE_PROMPT not in output


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_setup_user_interactive_postgres_is_not_confirmed(tmp_path, test_database):
    cwd = _dirs(tmp_path)
    code, output, stderr = _run_pty(
        ["--setup-user", "--db-url", test_database], cwd, _env(tmp_path)
    )
    assert code == 0, stderr
    assert CREATE_PROMPT not in output
    assert "User created in PostgreSQL database" in output


def test_setup_user_non_interactive_creates_without_asking(tmp_path):
    cwd = _dirs(tmp_path)
    result = _run(["--setup-user"], cwd, _env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert CREATE_PROMPT not in result.stdout
    assert (cwd / "scheduler-9456.db").is_file()


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:file:{tmp}/a.db?mode=rwc",
        "sqlite:file%3A{tmp}/a.db?mode=rwc",
        "sqlite:{tmp}/a.db?mode=rwc&cache=shared",
        "sqlite:{tmp}/a.db?cache=private",
        "sqlite:{tmp}/a.db?m%6Fde=rwc",
        "sqlite::memory:",
        "sqlite:%3Amemory%3A?mode=rwc",
        "sqlite:?mode=rwc",
    ],
    ids=[
        "file-uri",
        "encoded-file-uri",
        "two-parameters",
        "other-option",
        "encoded-key",
        "memory",
        "encoded-memory",
        "empty-path",
    ],
)
def test_setup_user_rejects_unsupported_sqlite_urls(tmp_path, url):
    cwd = _dirs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    url = url.format(tmp=target)
    result = _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path))
    assert result.returncode == 1
    assert result.stderr.splitlines() == [UNSUPPORTED.format(url=url)]
    assert list(target.iterdir()) == []
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize(
    "url, created",
    [
        ("sqlite:rel.db?mode=rwc", "{cwd}/rel.db"),
        ("sqlite://{tmp}/b.db?mode=rwc", "{tmp}/b.db"),
        ("sqlite:///{tmp_rel}/c.db", "{tmp}/c.db"),
        ("sqlite:{tmp}/a%20b.db?mode=rwc", "{tmp}/a b.db"),
    ],
    ids=["relative", "double-slash", "triple-slash-no-mode", "encoded-name"],
)
def test_setup_user_accepts_plain_sqlite_url_forms(tmp_path, url, created):
    cwd = _dirs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    url = url.format(tmp=target, tmp_rel=str(target).lstrip("/"))
    path = Path(created.format(cwd=cwd, tmp=target))
    if "mode=" not in url:
        prepare = f"sqlite:{path}?mode=rwc"
        assert (
            _run(["--setup-user", "--db-url", prepare], cwd, _env(tmp_path)).returncode
            == 0
        )
    result = _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"User database: SQLite at {path}"
    assert _users(path) == ["setup-admin"]
    if "%20" in url:
        assert not (target / "a%20b.db").exists()


def test_setup_user_unreadable_existing_database_is_not_confirmed(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("file permissions do not apply to root")
    cwd = _dirs(tmp_path)
    url = "sqlite:auth.db?mode=rwc"
    assert _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path)).returncode == 0
    (cwd / "auth.db").chmod(0)
    try:
        code, output, stderr = _run_pty(
            ["--setup-user", "--db-url", url], cwd, _env(tmp_path)
        )
    finally:
        (cwd / "auth.db").chmod(0o600)
    assert code == 1
    assert CREATE_PROMPT not in output
    assert "Failed to create database pool" in stderr
    assert "unable to open database file" in stderr


def test_detect_orphans_accepts_plain_url_and_rejects_unsupported(tmp_path):
    cwd = _dirs(tmp_path)
    db = tmp_path / "d.db"
    url = f"sqlite:{db}?mode=rwc"
    assert _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path)).returncode == 0
    accepted = _run(["--detect-orphans", "--db-url", url], cwd, _env(tmp_path))
    assert accepted.returncode == 0, accepted.stderr
    rejected_url = f"sqlite:{db}?mode=ro&cache=shared"
    rejected = _run(["--detect-orphans", "--db-url", rejected_url], cwd, _env(tmp_path))
    assert rejected.returncode == 2
    assert rejected.stderr.splitlines() == [UNSUPPORTED.format(url=rejected_url)]


def test_setup_user_missing_file_without_creating_mode_is_not_confirmed(tmp_path):
    cwd = _dirs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    url = f"sqlite:{target}/e.db?mode=rw"
    code, output, stderr = _run_pty(
        ["--setup-user", "--db-url", url], cwd, _env(tmp_path)
    )
    assert code == 1
    assert CREATE_PROMPT not in output
    assert "Failed to create database pool" in stderr
    assert "unable to open database file" in stderr
    assert list(target.iterdir()) == []


def test_setup_user_missing_parent_directory_is_not_confirmed(tmp_path):
    cwd = _dirs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    url = f"sqlite:{target}/missing/f.db?mode=rwc"
    code, output, stderr = _run_pty(
        ["--setup-user", "--db-url", url], cwd, _env(tmp_path)
    )
    assert code == 1
    assert CREATE_PROMPT not in output
    assert "Failed to create database pool" in stderr
    assert "unable to open database file" in stderr
    assert list(target.iterdir()) == []


def test_setup_user_eof_at_create_prompt_creates_nothing(tmp_path):
    cwd = _dirs(tmp_path)
    code, output, stderr = _run_pty(
        ["--setup-user"], cwd, _env(tmp_path), [(CREATE_PROMPT, "\x04")]
    )
    assert code == 1
    assert CREATE_PROMPT in output
    assert stderr.splitlines()[-1] == REFUSAL
    assert list(cwd.iterdir()) == []


def test_setup_user_dangling_symlink_is_confirmed_and_decline_keeps_it(tmp_path):
    cwd = _dirs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    link = target / "auth.db"
    link.symlink_to(target / "missing.db")
    code, output, stderr = _run_pty(
        ["--setup-user", "--db-url", f"sqlite:{link}?mode=rwc"],
        cwd,
        _env(tmp_path),
        [(CREATE_PROMPT, "n\n")],
    )
    assert code == 1
    assert CREATE_PROMPT in output
    assert stderr.splitlines()[-1] == REFUSAL
    assert os.readlink(link) == str(target / "missing.db")
    assert not (target / "missing.db").exists()


def test_setup_user_interactive_quickstart_creates_user(tmp_path):
    cwd = _dirs(tmp_path)
    code, output, stderr = _run_pty(
        ["--setup-user"],
        cwd,
        _env(tmp_path, credentials=False),
        [
            (CREATE_PROMPT, "\n"),
            ("Username: ", "quick-admin\n"),
            ("Password: ", f"{PASSWORD}\n"),
            ("Repeat password: ", f"{PASSWORD}\n"),
        ],
    )
    path = cwd / "scheduler-9456.db"
    assert code == 0, stderr
    assert f"User created in SQLite at {path}." in output
    with scheduler_context(db_url=f"sqlite:{path}?mode=rwc", create_user=False) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/auth/login",
            json={
                "username": "quick-admin",
                "password": PASSWORD,
                "delivery": "bearer",
            },
            headers=anonymous_headers(),
            timeout=30,
        )
        assert resp.status_code == 201
        assert resp.json()["token"] != ""


def test_setup_user_existing_user_is_not_offered_password_change(tmp_path):
    cwd = _dirs(tmp_path)
    assert _run(["--setup-user"], cwd, _env(tmp_path)).returncode == 0
    path = cwd / "scheduler-9456.db"
    location = f"SQLite at {path}"

    code, output, stderr = _run_pty(
        ["--setup-user"], cwd, _env(tmp_path, credentials=False)
    )
    assert code == 0, stderr
    assert "Change its password" not in output
    assert f"User exists in {location}; nothing to change." in output

    env = _env(tmp_path, AGENTBEACON_PASSWORD="a-brand-new-password")
    changed = _run(["--setup-user"], cwd, env)
    assert changed.returncode == 0, changed.stderr
    assert (
        f"Password changed in {location}. Every existing login was signed out."
        in changed.stdout
    )
    with scheduler_context(db_url=f"sqlite:{path}?mode=rwc", create_user=False) as ctx:

        def login(password):
            return httpx.post(
                f"{ctx['url']}/api/v1/auth/login",
                json={
                    "username": "setup-admin",
                    "password": password,
                    "delivery": "bearer",
                },
                headers=anonymous_headers(),
                timeout=30,
            )

        assert login("a-brand-new-password").status_code == 201
        assert_unauthorized(login(PASSWORD), LOGIN_FAILED_DETAIL)
