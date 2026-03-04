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
