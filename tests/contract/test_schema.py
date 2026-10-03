# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import re
import sqlite3

import pytest

from tests.testhelpers import db_conn, scheduler_context


EXPECTED_TABLES = [
    "schema_migrations",
    "config",
    "drivers",
    "agents",
    "projects",
    "project_agents",
    "executions",
    "execution_agents",
    "sessions",
    "events",
    "artifacts",
    "task_queue",
    "wiki_pages",
    "wiki_page_revisions",
    "wiki_tags",
    "wiki_page_tags",
    "wiki_subscriptions",
    "wiki_tag_members",
]

EXPECTED_COLUMNS = {
    "projects": [
        "id",
        "name",
        "slug",
        "path",
        "settings",
        "deleted_at",
        "created_at",
        "updated_at",
    ],
    "project_agents": [
        "project_id",
        "agent_id",
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
        "system_prompt",
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
        "title",
        "metadata",
        "max_depth",
        "max_width",
        "desired",
        "outcome",
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
        "worktree_path",
        "base_commit_sha",
        "metadata",
        "slug",
        "recovery_attempts",
        "desired",
        "executor_state",
        "outcome",
        "desired_by",
        "desired_at",
        "command_token",
        "command_type",
        "command_at",
        "command_has_payload",
        "worker_id",
        "parent_notified",
        "continued_from_session_id",
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
    "task_queue": [
        "id",
        "execution_id",
        "session_id",
        "task_payload",
        "queued_at",
        "source",
    ],
    "config": ["name", "value", "created_at", "updated_at"],
    "schema_migrations": ["version", "applied_at"],
    "wiki_pages": [
        "id",
        "project_id",
        "slug",
        "title",
        "body",
        "revision_number",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
        "deleted_at",
    ],
    "wiki_page_revisions": [
        "id",
        "page_id",
        "title",
        "body",
        "revision_number",
        "summary",
        "created_by",
        "created_at",
    ],
    "wiki_tags": ["id", "name"],
    "wiki_page_tags": ["page_id", "tag_id"],
    "wiki_subscriptions": [
        "id",
        "project_id",
        "subscriber",
        "page_slug",
        "tag_name",
        "created_at",
    ],
    "wiki_tag_members": [
        "id",
        "tag_id",
        "project_id",
        "access_level",
        "created_by",
        "created_at",
        "revoked_at",
    ],
}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_all_tables_present(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            if ctx["db_url"].startswith("sqlite:"):
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            else:
                cursor = conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
                )
            tables = [row[0] for row in cursor.fetchall()]

            for expected in EXPECTED_TABLES:
                assert expected in tables, (
                    f"Table '{expected}' not found. Present: {tables}"
                )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_table_columns(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            for table, expected_cols in EXPECTED_COLUMNS.items():
                if ctx["db_url"].startswith("sqlite:"):
                    cursor = conn.execute(f"PRAGMA table_info({table})")
                    actual_cols = [row[1] for row in cursor.fetchall()]
                else:
                    cursor = conn.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = %s "
                        "ORDER BY ordinal_position",
                        (table,),
                    )
                    actual_cols = [row[0] for row in cursor.fetchall()]

                for col in expected_cols:
                    assert col in actual_cols, (
                        f"Column '{col}' not found in table '{table}'. "
                        f"Actual columns: {actual_cols}"
                    )


def test_executions_desired_check_constraint():
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO executions (id, context_id, desired, sandbox_policy) VALUES ('test', 'test', 'invalid', '{\"fs_level\":\"unrestricted\"}')"
                )
        finally:
            conn.close()


def test_executions_outcome_check_constraint():
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO executions (id, context_id, outcome, sandbox_policy) VALUES ('test', 'test', 'invalid', '{\"fs_level\":\"unrestricted\"}')"
                )
        finally:
            conn.close()


def test_sessions_desired_check_constraint():
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO agents (id, name, agent_type, config) VALUES ('a-chk', 'chk-agent', 'acp', '{}')"
            )
            conn.execute(
                "INSERT INTO executions (id, context_id, sandbox_policy) VALUES ('e-chk', 'e-chk', '{\"fs_level\":\"unrestricted\"}')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO sessions (id, execution_id, agent_id, desired, sandbox_policy) VALUES ('s-chk', 'e-chk', 'a-chk', 'invalid', '{\"fs_level\":\"unrestricted\"}')"
                )
        finally:
            conn.close()


def test_sessions_executor_state_check_constraint():
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO agents (id, name, agent_type, config) VALUES ('a-chk2', 'chk-agent2', 'acp', '{}')"
            )
            conn.execute(
                "INSERT INTO executions (id, context_id, sandbox_policy) VALUES ('e-chk2', 'e-chk2', '{\"fs_level\":\"unrestricted\"}')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO sessions (id, execution_id, agent_id, executor_state, sandbox_policy) VALUES ('s-chk2', 'e-chk2', 'a-chk2', 'invalid', '{\"fs_level\":\"unrestricted\"}')"
                )
        finally:
            conn.close()


def test_sessions_command_type_check_constraint():
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO agents (id, name, agent_type, config) VALUES ('a-chk3', 'chk-agent3', 'acp', '{}')"
            )
            conn.execute(
                "INSERT INTO executions (id, context_id, sandbox_policy) VALUES ('e-chk3', 'e-chk3', '{\"fs_level\":\"unrestricted\"}')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO sessions (id, execution_id, agent_id, command_type, sandbox_policy) VALUES ('s-chk3', 'e-chk3', 'a-chk3', 'invalid', '{\"fs_level\":\"unrestricted\"}')"
                )
        finally:
            conn.close()


def test_agents_type_no_check_constraint():
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
    with scheduler_context() as ctx:
        db_path = ctx["db_path"]
        assert db_path is not None

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO agents (id, name, agent_type, config) VALUES ('a1', 'test-agent', 'acp', '{}')"
            )
            conn.execute(
                "INSERT INTO executions (id, context_id, sandbox_policy) VALUES ('e1', 'e1', '{\"fs_level\":\"unrestricted\"}')"
            )
            conn.execute(
                "INSERT INTO sessions (id, execution_id, agent_id, sandbox_policy) VALUES ('s1', 'e1', 'a1', '{\"fs_level\":\"unrestricted\"}')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES ('e1', 's1', 'invalid_type', '{}')"
                )
        finally:
            conn.close()


def test_indexes_present():
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
                "idx_project_agents_agent_id",
                "idx_projects_path",
                "idx_executions_project_id",
                "idx_executions_context_id",
                "idx_executions_created_at",
                "idx_sessions_execution_id",
                "idx_sessions_desired_executor_state",
                "idx_sessions_worker_id",
                "idx_events_execution_id_id",
                "idx_events_session_id_id",
                "idx_events_platform_id",
                "idx_events_execution_platform_id",
                "idx_artifacts_project_id",
                "idx_artifacts_session_id",
                "idx_task_queue_queued",
                "idx_wiki_tag_members_edge",
                "idx_wiki_tag_members_project_active",
                "idx_projects_slug_active",
            ]

            for idx in expected_indexes:
                assert idx in indexes, f"Index '{idx}' not found. Present: {indexes}"

            for idx in (
                "idx_events_execution_id",
                "idx_events_session_id",
                "idx_events_execution_timestamp",
                "idx_events_session_timestamp",
            ):
                assert idx not in indexes, f"Index '{idx}' should have been dropped"
        finally:
            conn.close()


EXPECTED_PARTIAL_INDEXES = {
    "idx_wiki_tag_members_edge": "revoked_at IS NULL",
    "idx_wiki_tag_members_project_active": "revoked_at IS NULL",
    "idx_projects_slug_active": "deleted_at IS NULL",
}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sharing_partial_indexes_present_on_both_dialects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            if ctx["db_url"].startswith("sqlite:"):
                cursor = conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
                )
            else:
                cursor = conn.execute(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public'"
                )
            definitions = {row[0]: row[1] or "" for row in cursor.fetchall()}

            for name, predicate in EXPECTED_PARTIAL_INDEXES.items():
                assert name in definitions, (
                    f"Index '{name}' not found. Present: {sorted(definitions)}"
                )
                sql = definitions[name].upper()
                assert re.search(rf"WHERE \(?{re.escape(predicate.upper())}\)?", sql), (
                    f"Index '{name}' is not filtered on '{predicate}': {definitions[name]}"
                )

            edge = definitions["idx_wiki_tag_members_edge"].upper()
            assert "UNIQUE" in edge
            slug = definitions["idx_projects_slug_active"].upper()
            assert "UNIQUE" in slug
            assert (
                "UNIQUE"
                not in definitions["idx_wiki_tag_members_project_active"].upper()
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_tag_members_access_level_check_constraint(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO projects (id, name, slug, path, settings) "
                "VALUES ('p-acl', 'acl', 'acl', '/tmp', '{}')"
            )
            conn.execute("INSERT INTO wiki_tags (id, name) VALUES ('t-acl', 'acl-tag')")
            conn.commit()

            with pytest.raises(Exception, match="(?i)check|constraint"):
                conn.execute(
                    "INSERT INTO wiki_tag_members (id, tag_id, project_id, access_level) "
                    "VALUES ('m-acl', 't-acl', 'p-acl', 'admin')"
                )
                conn.commit()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_tag_members_full_column_ddl(test_database):
    expected = {
        "id": (False, False),
        "tag_id": (False, False),
        "project_id": (False, False),
        "access_level": (False, False),
        "created_by": (True, False),
        "created_at": (False, True),
        "revoked_at": (True, False),
    }
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            if ctx["db_url"].startswith("sqlite:"):
                rows = conn.execute("PRAGMA table_info(wiki_tag_members)").fetchall()
                actual = {r[1]: (r[3] == 0, r[4] is not None) for r in rows}
                primary_keys = [r[1] for r in rows if r[5] > 0]
            else:
                rows = conn.execute(
                    "SELECT column_name, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'wiki_tag_members'"
                ).fetchall()
                actual = {r[0]: (r[1] == "YES", r[2] is not None) for r in rows}
                pk_rows = conn.execute(
                    "SELECT kcu.column_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON kcu.constraint_name = tc.constraint_name "
                    "WHERE tc.table_name = 'wiki_tag_members' "
                    "AND tc.constraint_type = 'PRIMARY KEY'"
                ).fetchall()
                primary_keys = [r[0] for r in pk_rows]

            assert actual == expected
            assert primary_keys == ["id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_wiki_tag_members_foreign_keys(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            if ctx["db_url"].startswith("sqlite:"):
                rows = conn.execute(
                    "PRAGMA foreign_key_list(wiki_tag_members)"
                ).fetchall()
                actual = {r[3]: (r[2], r[4]) for r in rows}
            else:
                rows = conn.execute(
                    "SELECT kcu.column_name, ccu.table_name, ccu.column_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON kcu.constraint_name = tc.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON ccu.constraint_name = tc.constraint_name "
                    "WHERE tc.table_name = 'wiki_tag_members' "
                    "AND tc.constraint_type = 'FOREIGN KEY'"
                ).fetchall()
                actual = {r[0]: (r[1], r[2]) for r in rows}

            assert actual == {
                "tag_id": ("wiki_tags", "id"),
                "project_id": ("projects", "id"),
            }


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_projects_slug_is_not_null(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        with db_conn(ctx["db_url"]) as conn:
            with pytest.raises(Exception, match="(?i)not null|null value"):
                conn.execute(
                    "INSERT INTO projects (id, name, path, settings) "
                    "VALUES ('p-noslug', 'noslug', '/tmp', '{}')"
                )
                conn.commit()
