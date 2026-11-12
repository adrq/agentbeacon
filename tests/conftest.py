# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared fixtures for all test directories.

Provides database backend fixtures that enable testing against both
SQLite and PostgreSQL automatically via pytest parametrization.
"""

import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import pytest
import psycopg2
import httpx
import requests

from tests.testhelpers import ANONYMOUS_HEADER, injected_authorization


@pytest.fixture
def test_database(request):
    """Fixture providing database URL for both SQLite and PostgreSQL.

    Always tests both backends - no env var required for basic operation.
    PostgreSQL URL can be overridden via POSTGRES_TEST_URL env var.

    Args:
        request: Pytest request object containing parametrized value

    Yields:
        str: Database URL in format: sqlite://path?mode=rwc or postgres://...
    """
    backend = request.param

    if backend == "sqlite":
        # Create temporary SQLite database
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        # SQLite needs ?mode=rwc to create the file if it doesn't exist
        db_url = f"sqlite:{temp_db.name}?mode=rwc"
        yield db_url

        # Cleanup
        try:
            Path(temp_db.name).unlink()
        except FileNotFoundError:
            pass

    elif backend == "postgres":
        base_url = os.environ.get(
            "POSTGRES_TEST_URL",
            "postgres://postgres:postgres@127.0.0.1/agentbeacon_test",
        )
        parsed = urlparse(base_url)
        db_name = f"agentbeacon_test_{uuid.uuid4().hex[:8]}"

        # Connect to admin DB by swapping path to /postgres
        admin_url = urlunparse(parsed._replace(path="/postgres"))
        admin_conn = psycopg2.connect(admin_url)
        admin_conn.autocommit = True
        cur = admin_conn.cursor()
        cur.execute(f"CREATE DATABASE {db_name}")
        cur.close()

        # Yield test DB URL by swapping path to the new database
        db_url = urlunparse(parsed._replace(path=f"/{db_name}"))
        yield db_url

        # Teardown: terminate connections then drop
        cur = admin_conn.cursor()
        cur.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}'"
        )
        cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
        cur.close()
        admin_conn.close()

    else:
        raise ValueError(f"Unknown backend: {backend}")


def _with_injected_auth(request_url, path, headers, content):
    """Apply the harness credential rules to one outgoing request's headers."""
    if ANONYMOUS_HEADER in headers:
        del headers[ANONYMOUS_HEADER]
        return
    if "authorization" in headers or "cookie" in headers:
        return
    value = injected_authorization(request_url, path, headers, content)
    if value is not None:
        headers["Authorization"] = value


@pytest.fixture(autouse=True, scope="session")
def _isolated_data_dir(tmp_path_factory):
    """Give every process the tests start a private AGENTBEACON_DATA_DIR."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGENTBEACON_DATA_DIR", str(tmp_path_factory.mktemp("data")))
        yield


@pytest.fixture(autouse=True, scope="session")
def _inject_test_credentials():
    """Send the harness's credentials with requests to test schedulers.

    Requests that carry their own Authorization header, a cookie, or the
    anonymous marker are left as they are (the marker is removed).
    """
    original_sync_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send
    original_requests_send = requests.Session.send

    def _content(request):
        try:
            return request.content
        except httpx.RequestNotRead:
            return b""

    def sync_send(self, request, *args, **kwargs):
        _with_injected_auth(
            str(request.url), request.url.path, request.headers, _content(request)
        )
        return original_sync_send(self, request, *args, **kwargs)

    async def async_send(self, request, *args, **kwargs):
        _with_injected_auth(
            str(request.url), request.url.path, request.headers, _content(request)
        )
        return await original_async_send(self, request, *args, **kwargs)

    def requests_send(self, request, *args, **kwargs):
        body = request.body
        if isinstance(body, str):
            body = body.encode()
        _with_injected_auth(
            request.url, request.path_url.split("?")[0], request.headers, body or b""
        )
        return original_requests_send(self, request, *args, **kwargs)

    httpx.Client.send = sync_send
    httpx.AsyncClient.send = async_send
    requests.Session.send = requests_send
    try:
        yield
    finally:
        httpx.Client.send = original_sync_send
        httpx.AsyncClient.send = original_async_send
        requests.Session.send = original_requests_send
