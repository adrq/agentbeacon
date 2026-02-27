"""Contract test: verify all target tables exist with correct structure.

Uses a real scheduler instance to verify the migration produces the expected schema.
"""

import sqlite3
import pytest

from tests.testhelpers import scheduler_context


EXPECTED_TABLES = [
    "schema_migrations",
    "config",
    "drivers",
    "agents",
    "projects",
    "executions",
    "execution_agents",
    "sessions",
    "events",
    "artifacts",
    "task_queue",
]

EXPECTED_COLUMNS = {
    "projects": [
        "id",
        "name",
        "path",
        "default_agent_id",
        "settings",
        "deleted_at",
        "created_at",
        "updated_at",
    ],
    "drivers": [
        "id",
        "name",
        "platform",
        "config",
        "created_at",
        "updated_at",
    ],
    "agents": [
        "id",
        "name",
        "description",
        "agent_type",
        "driver_id",
        "config",
        "sandbox_config",
        "enabled",
        "deleted_at",
        "created_at",
        "updated_at",
    ],
    "execution_agents": [
        "execution_id",
        "agent_id",
    ],
    "executions": [
        "id",
        "project_id",
        "parent_execution_id",
        "context_id",
        "worktree_path",
        "status",
        "title",
        "input",
        "metadata",
        "created_at",
        "updated_at",
        "completed_at",
    ],
    "sessions": [
        "id",
        "execution_id",
        "parent_session_id",
        "agent_id",
        "agent_session_id",
        "cwd",
        "status",
        "coordination_mode",
        "metadata",
        "created_at",
        "updated_at",
        "completed_at",
    ],
    "events": [
        "id",
        "execution_id",
        "session_id",
        "event_type",
        "payload",
        "created_at",
    ],
    "artifacts": [
        "id",
        "project_id",
        "session_id",
        "artifact_type",
        "name",
        "description",
        "reference",
        "metadata",
        "created_at",
    ],
    "task_queue": ["id", "execution_id", "session_id", "task_payload", "queued_at"],
    "config": ["name", "value", "created_at", "updated_at"],
    "schema_migrations": ["version", "applied_at"],
}


def test_all_tables_present():
    """Verify all 9 target tables exist after migration."""
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None, "Expected SQLite database for this test"

        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]

            for expected in EXPECTED_TABLES:
                assert expected in tables, (
                    f"Table '{expected}' not found. Present: {tables}"
                )
        finally:
            conn.close()


def test_table_columns():
    """Verify key columns exist on each table."""
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            for table, expected_cols in EXPECTED_COLUMNS.items():
                cursor = conn.execute(f"PRAGMA table_info({table})")
                actual_cols = [row[1] for row in cursor.fetchall()]

                for col in expected_cols:
                    assert col in actual_cols, (
                        f"Column '{col}' not found in table '{table}'. "
                        f"Actual columns: {actual_cols}"
                    )
        finally:
            conn.close()


def test_executions_status_check_constraint():
    """Verify CHECK constraint on executions.status rejects invalid values."""
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO executions (id, context_id, status, input) VALUES ('test', 'test', 'invalid_status', '{}')"
                )
        finally:
            conn.close()


def test_agents_type_no_check_constraint():
    """After DM migration, agent_type CHECK constraint is removed.

    Platform validation now happens via drivers.platform at the API layer.
    The DB allows any agent_type value.
    """
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO agents (id, name, agent_type, config) VALUES ('test', 'test', 'invalid_type', '{}')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT agent_type FROM agents WHERE id = 'test'"
            ).fetchone()
            assert row[0] == "invalid_type"
        finally:
            conn.execute("DELETE FROM agents WHERE id = 'test'")
            conn.commit()
            conn.close()


def test_events_type_check_constraint():
    """Verify CHECK constraint on events.event_type rejects invalid values."""
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            # Need to create parent records first for FK
            conn.execute(
                "INSERT INTO agents (id, name, agent_type, config) VALUES ('a1', 'test-agent', 'acp', '{}')"
            )
            conn.execute(
                "INSERT INTO executions (id, context_id, status, input) VALUES ('e1', 'e1', 'submitted', '{}')"
            )
            conn.execute(
                "INSERT INTO sessions (id, execution_id, agent_id, status) VALUES ('s1', 'e1', 'a1', 'submitted')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES ('e1', 's1', 'invalid_type', '{}')"
                )
        finally:
            conn.close()


def test_indexes_present():
    """Verify key indexes exist."""
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
            )
            indexes = [row[0] for row in cursor.fetchall()]

            expected_indexes = [
                "idx_agents_enabled",
                "idx_agents_name_active",
                "idx_agents_driver_id",
                "idx_execution_agents_agent_id",
                "idx_projects_path",
                "idx_executions_project_id",
                "idx_executions_status",
                "idx_executions_context_id",
                "idx_executions_created_at",
                "idx_sessions_execution_id",
                "idx_sessions_status",
                "idx_events_execution_id",
                "idx_events_session_id",
                "idx_events_execution_timestamp",
                "idx_events_session_timestamp",
                "idx_artifacts_project_id",
                "idx_artifacts_session_id",
                "idx_task_queue_queued",
            ]

            for idx in expected_indexes:
                assert idx in indexes, f"Index '{idx}' not found. Present: {indexes}"
        finally:
            conn.close()
