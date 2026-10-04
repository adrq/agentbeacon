# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import db_conn, scheduler_context


def expected_tag(db_url):
    with db_conn(db_url) as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[
            0
        ]
    return f"agentbeacon-schema{version}"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_pg_connections_tagged_with_schema_version(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        httpx.get(f"{ctx['url']}/api/v1/executions", timeout=10)

        with db_conn(test_database) as conn:
            rows = conn.execute(
                "SELECT DISTINCT application_name FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND application_name LIKE 'agentbeacon-schema%'"
            ).fetchall()

        names = {r[0] for r in rows}
        tag = expected_tag(test_database)
        assert tag in names, (
            f"expected the scheduler's PG connections tagged {tag}, saw {names}"
        )


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_pg_application_name_overrides_caller_value(test_database):
    sep = "&" if "?" in test_database else "?"
    tagged_url = f"{test_database}{sep}application_name=legacy"
    with scheduler_context(db_url=tagged_url) as ctx:
        httpx.get(f"{ctx['url']}/api/v1/executions", timeout=10)
        with db_conn(test_database) as conn:
            rows = conn.execute(
                "SELECT DISTINCT application_name FROM pg_stat_activity "
                "WHERE datname = current_database()"
            ).fetchall()
    names = {r[0] for r in rows}
    assert expected_tag(test_database) in names
    assert "legacy" not in names, "caller application_name must be replaced, not kept"
