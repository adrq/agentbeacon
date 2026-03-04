"""
Shared fixtures for all test directories.

Provides database backend fixtures that enable testing against both
SQLite and PostgreSQL automatically via pytest parametrization.
"""

import os
import tempfile
from pathlib import Path
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
        # Hardcoded default, optional env override
        base_url = os.environ.get(
            "POSTGRES_TEST_URL",
            "postgres://postgres:postgres@127.0.0.1/agentbeacon_test",
        )
        yield base_url
        # Cleanup is now handled by cleanup_postgres_database fixture

    else:
        raise ValueError(f"Unknown backend: {backend}")


@pytest.fixture(autouse=True, scope="function")
def cleanup_postgres_database(request):
    """Clean PostgreSQL database after each test for idempotency.

    SQLite tests use temporary files and are already isolated.
    PostgreSQL tests share agentbeacon_test database and need cleanup.

    Truncates all application tables while preserving schema_migrations
    to maintain migration history.
    """
    # Only run if test uses test_database fixture
    if "test_database" not in request.fixturenames:
        yield
        return

    # Get the database URL from the test_database fixture
    test_database = request.getfixturevalue("test_database")

    yield  # Let test run first

    # Only cleanup PostgreSQL (SQLite already isolated via temp files)
    if not test_database.startswith("postgres"):
        return

    try:
        # Connect directly to PostgreSQL for cleanup
        conn = psycopg2.connect(test_database)
        try:
            cur = conn.cursor()

            # Truncate all tables in reverse dependency order
            # CASCADE handles any remaining foreign key constraints
            tables = [
                "wiki_subscriptions",  # Child of projects
                "wiki_page_tags",  # Child of wiki_pages and wiki_tags
                "wiki_tags",  # After wiki_page_tags
                "wiki_page_revisions",  # Child of wiki_pages
                "wiki_pages",  # Child of projects
                "events",  # Child of sessions
                "artifacts",  # Child of projects/sessions
                "task_queue",  # Child of executions/sessions
                "execution_agents",  # Child of executions/agents
                "sessions",  # Child of executions
                "executions",  # Child of projects
                "agents",  # References drivers
                "drivers",  # Standalone
                "projects",  # Standalone
                "config",  # Standalone
            ]

            # Note: schema_migrations is NOT truncated to preserve migration history

            for table in tables:
                cur.execute(f"TRUNCATE TABLE {table} CASCADE")

            # Re-seed migration defaults for config (truncated above)
            cur.execute(
                "INSERT INTO config (name, value, created_at, updated_at) "
                "VALUES ('max_depth', '2', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "       ('max_width', '5', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
            )

            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        # Best-effort cleanup - log but don't fail test teardown
        print(f"Warning: PostgreSQL cleanup failed: {e}")
