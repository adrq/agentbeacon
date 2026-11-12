# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Default storage of the packaged binaries, under the data directory.

On Linux these tests use the host-architecture musl binary: make build-musl-x64 or build-musl-arm64
Run with: uv run pytest -v -m musl

On an Apple Silicon Mac, after make build-macos-native:
    AGENTBEACON_TEST_RELEASE_BIN=target/aarch64-apple-darwin/release/agentbeacon \\
        uv run pytest -v -m packaging tests/test_release_storage.py

When AGENTBEACON_TEST_RELEASE_BIN is set, a missing binary fails every test instead of skipping.
"""

import json
import os
import platform
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from tests.testhelpers import PortManager, anonymous_headers, run_pty

BASE_DIR = Path(__file__).parent.parent
RELEASE_BIN_ENV = "AGENTBEACON_TEST_RELEASE_BIN"
EXPLICIT_BIN = os.environ.get(RELEASE_BIN_ENV)
if sys.platform == "darwin":
    TRIPLE = "aarch64-apple-darwin"
else:
    TRIPLE = f"{platform.machine()}-unknown-linux-musl"
if EXPLICIT_BIN is None:
    BIN = BASE_DIR / "target" / TRIPLE / "release" / "agentbeacon"
elif EXPLICIT_BIN:
    BIN = Path(EXPLICIT_BIN).resolve()
else:
    BIN = Path(EXPLICIT_BIN)
if sys.platform == "darwin":
    DEFAULT_DATA_DIR = Path("Library") / "Application Support" / "agentbeacon"
else:
    DEFAULT_DATA_DIR = Path(".local") / "share" / "agentbeacon"
PASSWORD = "release-password"
NOT_UTF8 = "AGENTBEACON_DATA_DIR is not valid UTF-8"
CREATE_PROMPT = "Create it?"

pytestmark = [
    pytest.mark.musl,
    pytest.mark.packaging,
    pytest.mark.skipif(
        EXPLICIT_BIN is None and not BIN.is_file(),
        reason=f"release binary not built: {BIN}",
    ),
]


@pytest.fixture(autouse=True)
def _require_explicit_binary():
    if EXPLICIT_BIN is not None and not BIN.is_file():
        pytest.fail(f"{RELEASE_BIN_ENV} is set but {BIN} is not a file")


def _env(tmp_path, credentials=True, data=True, **extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "DATABASE_URL",
            "AGENTBEACON_WIKI_INDEX_DIR",
            "AGENTBEACON_PORT",
            "AGENTBEACON_USERNAME",
            "AGENTBEACON_PASSWORD",
            "AGENTBEACON_DATA_DIR",
            "XDG_DATA_HOME",
        )
    }
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    if data:
        env["AGENTBEACON_DATA_DIR"] = str(tmp_path / "data")
    if credentials:
        env["AGENTBEACON_USERNAME"] = "release-admin"
        env["AGENTBEACON_PASSWORD"] = PASSWORD
    env.update(extra)
    return env


def _non_utf8_env(env, tmp_path):
    out = {k.encode(): v.encode() for k, v in env.items()}
    out[b"AGENTBEACON_DATA_DIR"] = bytes(tmp_path) + b"/\xff"
    return out


def _umask_022():
    os.umask(0o022)


def _run(args, cwd, env, timeout=60):
    return subprocess.run(
        [str(BIN), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        preexec_fn=_umask_022,
    )


def _workdir(tmp_path, name="cwd"):
    path = tmp_path / name
    path.mkdir()
    return path


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def port():
    ports = PortManager()
    allocated = ports.allocate_port()
    yield allocated
    ports.release_port(allocated)


@contextmanager
def _serving(cwd, env, port, *args):
    log = cwd.parent / f"server-{port}.log"
    with open(log, "w") as out:
        proc = subprocess.Popen(
            [str(BIN), "--port", str(port), "--max-workers", "0", *args],
            cwd=cwd,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            preexec_fn=_umask_022,
        )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited: {log.read_text()}")
            try:
                resp = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
                if resp.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            pytest.fail(f"server not ready: {log.read_text()}")
        yield f"http://127.0.0.1:{port}", log
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.unlink(missing_ok=True)


def _login(url, password=PASSWORD):
    return httpx.post(
        f"{url}/api/v1/auth/login",
        json={"username": "release-admin", "password": password, "delivery": "bearer"},
        headers=anonymous_headers(),
        timeout=30,
    )


def _assert_logged_in(url, password=PASSWORD):
    resp = _login(url, password)
    assert resp.status_code == 201
    token = resp.json()["token"]
    assert token != ""
    logins = httpx.get(
        f"{url}/api/v1/auth/logins",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert logins.status_code == 200


def _messages(log_text):
    out = []
    for line in log_text.splitlines():
        try:
            out.append(json.loads(line)["fields"]["message"])
        except (ValueError, KeyError):
            continue
    return out


def _default_db(tmp_path, port):
    return tmp_path / "data" / "db" / f"scheduler-{port}.db"


def test_release_setup_user_and_start_share_database_across_directories(tmp_path, port):
    a = _workdir(tmp_path, "a")
    b = _workdir(tmp_path, "b")
    setup = _run(["--setup-user", "--port", str(port)], a, _env(tmp_path))
    assert setup.returncode == 0, setup.stderr
    with _serving(b, _env(tmp_path, credentials=False), port) as (url, _):
        _assert_logged_in(url)
    assert list(a.iterdir()) == []
    assert list(b.iterdir()) == []


def test_release_default_paths_are_in_data_dir_db(tmp_path, port):
    cwd = _workdir(tmp_path)
    setup = _run(["--setup-user", "--port", str(port)], cwd, _env(tmp_path))
    assert setup.returncode == 0, setup.stderr
    db = _default_db(tmp_path, port)
    assert setup.stdout.splitlines()[0] == f"User database: SQLite at {db}"
    with _serving(cwd, _env(tmp_path, credentials=False), port):
        pass
    assert db.is_file()
    assert (tmp_path / "data" / "db" / f"wiki-index-{port}").is_dir()


def test_release_default_uses_platform_data_dir_without_override(tmp_path):
    cwd = _workdir(tmp_path)
    setup = _run(["--setup-user"], cwd, _env(tmp_path, data=False))
    assert setup.returncode == 0, setup.stderr
    db = tmp_path / "home" / DEFAULT_DATA_DIR / "db" / "scheduler-9456.db"
    assert setup.stdout.splitlines()[0] == f"User database: SQLite at {db}"
    assert db.is_file()


def test_release_database_dir_and_file_are_private(tmp_path):
    cwd = _workdir(tmp_path)
    assert _run(["--setup-user"], cwd, _env(tmp_path)).returncode == 0
    assert _mode(tmp_path / "data" / "db") == 0o700
    assert _mode(_default_db(tmp_path, 9456)) == 0o600


def test_release_existing_database_is_kept(tmp_path, port):
    cwd = _workdir(tmp_path)
    assert (
        _run(["--setup-user", "--port", str(port)], cwd, _env(tmp_path)).returncode == 0
    )
    again = _run(
        ["--setup-user", "--port", str(port)], cwd, _env(tmp_path, credentials=False)
    )
    assert again.returncode == 0, again.stderr
    location = f"SQLite at {_default_db(tmp_path, port)}"
    assert f"User exists in {location}; nothing to change." in again.stdout.splitlines()
    with _serving(cwd, _env(tmp_path, credentials=False), port) as (url, _):
        _assert_logged_in(url)


def test_release_rejects_non_utf8_data_dir(tmp_path):
    cwd = _workdir(tmp_path)
    env = _non_utf8_env(_env(tmp_path), tmp_path)
    setup = subprocess.run(
        [str(BIN), "--setup-user"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert setup.returncode == 1
    assert setup.stderr.splitlines() == [NOT_UTF8]
    detect = subprocess.run(
        [str(BIN), "--detect-orphans"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert detect.returncode == 2
    assert detect.stderr.splitlines() == [NOT_UTF8]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cwd", "home"]
    assert list(cwd.iterdir()) == []
    assert list((tmp_path / "home").iterdir()) == []


def test_release_rejects_data_dir_the_url_cannot_express(tmp_path):
    cwd = _workdir(tmp_path)
    data = tmp_path / "a?b"
    setup = _run(["--setup-user"], cwd, _env(tmp_path, AGENTBEACON_DATA_DIR=str(data)))
    assert setup.returncode == 1
    path = data / "db" / "scheduler-9456.db"
    assert setup.stderr.splitlines() == [
        f'the database path "{path}" cannot be used in a database URL; '
        "set AGENTBEACON_DATA_DIR to a different directory or pass --db-url"
    ]
    assert not data.exists()
    assert not (tmp_path / "a").exists()


def test_release_unwritable_data_dir_fails(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("file permissions do not apply to root")
    cwd = _workdir(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        data = locked / "data"
        setup = _run(
            ["--setup-user"], cwd, _env(tmp_path, AGENTBEACON_DATA_DIR=str(data))
        )
    finally:
        locked.chmod(0o755)
    assert setup.returncode == 1
    error = setup.stderr.splitlines()[-1]
    assert error.startswith(f"cannot create {data} for the database: ")
    assert error.endswith("; set AGENTBEACON_DATA_DIR or pass --db-url")


def test_release_explicit_db_and_index_ignore_unusable_data_dir(tmp_path, port):
    cwd = _workdir(tmp_path)
    url = f"sqlite:{tmp_path / 'explicit.db'}?mode=rwc"
    setup = _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path))
    assert setup.returncode == 0, setup.stderr
    env = _non_utf8_env(_env(tmp_path, credentials=False), tmp_path)
    with _serving(
        cwd, env, port, "--db-url", url, "--wiki-index-dir", str(tmp_path / "index")
    ) as (server, _):
        _assert_logged_in(server)


def test_release_explicit_db_with_default_index_rejects_non_utf8_data_dir(
    tmp_path, port
):
    cwd = _workdir(tmp_path)
    url = f"sqlite:{tmp_path / 'explicit.db'}?mode=rwc"
    env = _non_utf8_env(_env(tmp_path, credentials=False), tmp_path)
    start = subprocess.run(
        [str(BIN), "--port", str(port), "--max-workers", "0", "--db-url", url],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert start.returncode == 1
    assert start.stderr.splitlines()[-1] == f"Error: {NOT_UTF8}"


def test_release_detect_orphans_uses_default_database(tmp_path, port):
    a = _workdir(tmp_path, "a")
    b = _workdir(tmp_path, "b")
    empty = tmp_path / "empty"
    missing = _run(
        ["--detect-orphans", "--port", str(port)],
        b,
        _env(tmp_path, AGENTBEACON_DATA_DIR=str(empty)),
    )
    assert missing.returncode == 2
    assert not empty.exists()
    assert (
        _run(["--setup-user", "--port", str(port)], a, _env(tmp_path)).returncode == 0
    )
    found = _run(["--detect-orphans", "--port", str(port)], b, _env(tmp_path))
    assert found.returncode == 0, found.stderr


def test_release_setup_opens_no_database(tmp_path):
    cwd = _workdir(tmp_path)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    npm = stub_dir / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n")
    npm.chmod(0o755)
    env = _env(tmp_path, credentials=False)
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    setup = _run(["--setup"], cwd, env)
    assert setup.returncode == 0, setup.stderr
    assert not (tmp_path / "data" / "db").exists()


def test_release_explicit_db_with_unwritable_default_index_fails(tmp_path, port):
    if os.geteuid() == 0:
        pytest.skip("file permissions do not apply to root")
    cwd = _workdir(tmp_path)
    url = f"sqlite:{tmp_path / 'explicit.db'}?mode=rwc"
    assert _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path)).returncode == 0
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    data = locked / "data"
    try:
        start = _run(
            ["--port", str(port), "--max-workers", "0", "--db-url", url],
            cwd,
            _env(tmp_path, credentials=False, AGENTBEACON_DATA_DIR=str(data)),
        )
    finally:
        locked.chmod(0o755)
    assert start.returncode == 1
    error = start.stderr.splitlines()[-1]
    assert error.startswith(f"Error: cannot create {data} for the wiki search index: ")
    assert error.endswith("; set AGENTBEACON_DATA_DIR or pass --wiki-index-dir")


def test_release_explicit_db_creates_default_index_dir_private(tmp_path, port):
    cwd = _workdir(tmp_path)
    url = f"sqlite:{tmp_path / 'explicit.db'}?mode=rwc"
    assert _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path)).returncode == 0
    with _serving(cwd, _env(tmp_path, credentials=False), port, "--db-url", url):
        pass
    db_dir = tmp_path / "data" / "db"
    assert _mode(db_dir) == 0o700
    assert (db_dir / f"wiki-index-{port}").is_dir()
    assert list(db_dir.glob("scheduler-*.db")) == []


def test_release_existing_modes_are_not_changed(tmp_path, port):
    cwd = _workdir(tmp_path)
    assert (
        _run(["--setup-user", "--port", str(port)], cwd, _env(tmp_path)).returncode == 0
    )
    db_dir = tmp_path / "data" / "db"
    db = _default_db(tmp_path, port)
    db_dir.chmod(0o750)
    db.chmod(0o640)
    again = _run(
        ["--setup-user", "--port", str(port)], cwd, _env(tmp_path, credentials=False)
    )
    assert again.returncode == 0, again.stderr
    with _serving(cwd, _env(tmp_path, credentials=False), port):
        pass
    assert _mode(db_dir) == 0o750
    assert _mode(db) == 0o640


def test_release_explicit_sqlite_modes_are_not_changed(tmp_path, port):
    cwd = _workdir(tmp_path)
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    explicit.chmod(0o755)
    existing = explicit / "existing.db"
    url = f"sqlite:{existing}?mode=rwc"
    assert _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path)).returncode == 0
    existing.chmod(0o644)
    assert _run(["--setup-user", "--db-url", url], cwd, _env(tmp_path)).returncode == 0
    with _serving(cwd, _env(tmp_path, credentials=False), port, "--db-url", url):
        pass
    assert _mode(explicit) == 0o755
    assert _mode(existing) == 0o644

    created = explicit / "new.db"
    new_url = f"sqlite:{created}?mode=rwc"
    assert (
        _run(["--setup-user", "--db-url", new_url], cwd, _env(tmp_path)).returncode == 0
    )
    assert _mode(created) == 0o644


def test_release_interactive_decline_creates_nothing(tmp_path):
    cwd = _workdir(tmp_path)
    code, output, stderr = run_pty(
        [str(BIN), "--setup-user"], cwd, _env(tmp_path), [(CREATE_PROMPT, "n\n")]
    )
    db = _default_db(tmp_path, 9456)
    assert code == 1
    assert CREATE_PROMPT in output
    assert stderr.splitlines() == [
        f"warning: no database URL given; using SQLite at {db}. To use a different database, "
        "pass --db-url or set DATABASE_URL, here and when starting AgentBeacon.",
        "No database was created. Pass --db-url or set DATABASE_URL to use a different database.",
    ]
    assert not (tmp_path / "data" / "db").exists()


def test_release_relative_data_dir_override_is_a_plain_path(tmp_path):
    cwd = _workdir(tmp_path)
    setup = _run(["--setup-user"], cwd, _env(tmp_path, AGENTBEACON_DATA_DIR="file:123"))
    assert setup.returncode == 0, setup.stderr
    db = cwd / "file:123" / "db" / "scheduler-9456.db"
    assert setup.stdout.splitlines()[0] == f"User database: SQLite at {db}"
    assert _mode(db) == 0o600
    assert [p.name for p in cwd.iterdir()] == ["file:123"]


def test_release_start_without_setup_creates_private_default_storage(tmp_path, port):
    cwd = _workdir(tmp_path)
    with _serving(cwd, _env(tmp_path, credentials=False), port) as (_, log):
        messages = _messages(log.read_text())
    db_dir = tmp_path / "data" / "db"
    db = _default_db(tmp_path, port)
    assert _mode(db_dir) == 0o700
    assert _mode(db) == 0o600
    assert (db_dir / f"wiki-index-{port}").is_dir()
    assert (
        f"No user exists in SQLite at {db}; run `agentbeacon --setup-user` "
        "with the same --port or --db-url to create one"
    ) in messages
    assert list(cwd.iterdir()) == []


def test_release_dangling_default_database_link_is_refused(tmp_path, port):
    cwd = _workdir(tmp_path)
    db_dir = tmp_path / "data" / "db"
    db_dir.mkdir(parents=True)
    db_dir.chmod(0o700)
    elsewhere = tmp_path / "elsewhere.db"
    db = _default_db(tmp_path, port)
    db.symlink_to(elsewhere)
    expected = (
        f"cannot create {db} for the database: File exists (os error 17); "
        "set AGENTBEACON_DATA_DIR or pass --db-url"
    )

    start = _run(
        ["--port", str(port), "--max-workers", "0"],
        cwd,
        _env(tmp_path, credentials=False),
    )
    assert start.returncode == 1
    assert start.stderr.splitlines()[-1] == f"Error: {expected}"

    code, output, stderr = run_pty(
        [str(BIN), "--setup-user", "--port", str(port)],
        cwd,
        _env(tmp_path),
        [(CREATE_PROMPT, "\n")],
    )
    assert code == 1
    assert CREATE_PROMPT in output
    assert stderr.splitlines()[-1] == expected
    assert not elsewhere.exists()
    assert os.readlink(db) == str(elsewhere)
