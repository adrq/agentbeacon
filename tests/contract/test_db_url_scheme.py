"""Contract tests: verify database URL scheme recognition.

Tests that the scheduler correctly accepts SQLite and PostgreSQL URLs
and rejects unsupported schemes like mysql://.
"""

import re
import subprocess
from pathlib import Path

import pytest

from tests.testhelpers import scheduler_context


def test_scheduler_starts_with_sqlite_url():
    """Scheduler starts successfully with default SQLite URL."""
    with scheduler_context() as ctx:
        assert ctx["process"].poll() is None, "Scheduler should be running"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_scheduler_starts_with_postgres_url(test_database):
    """Scheduler starts successfully with postgres:// URL."""
    with scheduler_context(db_url=test_database) as ctx:
        assert ctx["process"].poll() is None, "Scheduler should be running"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_scheduler_starts_with_postgresql_url(test_database):
    """Scheduler starts successfully with postgresql:// (RFC 3986) URL."""
    url = re.sub(r"^postgres://", "postgresql://", test_database)
    assert url.startswith("postgresql://"), f"Expected postgresql:// URL, got: {url}"

    with scheduler_context(db_url=url) as ctx:
        assert ctx["process"].poll() is None, "Scheduler should be running"


def test_scheduler_rejects_invalid_url_scheme():
    """Scheduler exits with error for unsupported database URL schemes."""
    base_dir = Path(__file__).parent.parent.parent
    binary = base_dir / "bin" / "agentbeacon-scheduler"

    proc = subprocess.Popen(
        [str(binary), "--port", "0", "--db-url", "mysql://localhost/test"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Scheduler should have exited quickly for invalid URL")

    assert proc.returncode != 0, "Scheduler should exit with non-zero code"

    stderr = proc.stderr.read().decode()
    assert "Unsupported database URL" in stderr, (
        f"Expected 'Unsupported database URL' in stderr, got: {stderr}"
    )
