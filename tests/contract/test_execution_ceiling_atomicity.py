# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

from tests.contract.test_event_write_failpoints import _EventInsertFails
from tests.contract.test_execution_ceiling import (
    EGRESS,
    NO_WORKERS,
    NONE,
    _allowlist,
    _ceiling_events,
    _ceiling_row,
    _create,
    _create_sandboxed,
    _execution_count,
    _patch,
)
from tests.dual_backend import DUAL_BACKEND
from tests.testhelpers import scheduler_context, seed_test_agent


@DUAL_BACKEND
def test_create_is_atomic_when_launch_event_insert_fails(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        ctx = {**ctx, "agent_id": seed_test_agent(ctx["db_url"], name="atomic")}
        before = _execution_count(ctx)
        with _EventInsertFails(ctx["db_url"], "platform"):
            resp = _create(ctx, _allowlist("github.com:443"))
        assert resp.status_code == 500
        assert "insert event failed" in resp.text
        assert "failpoint" in resp.text
        assert _execution_count(ctx) == before


@DUAL_BACKEND
def test_patch_is_atomic_when_event_insert_fails(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        ctx = {**ctx, "agent_id": seed_test_agent(ctx["db_url"], name="atomic")}
        exec_id = _create_sandboxed(ctx, EGRESS)["execution"]["id"]
        before_row = _ceiling_row(ctx, exec_id)
        with _EventInsertFails(ctx["db_url"], "platform"):
            resp = _patch(ctx, exec_id, {"network": NONE})
        assert resp.status_code == 500
        assert "insert event failed" in resp.text
        assert "failpoint" in resp.text
        assert _ceiling_row(ctx, exec_id) == before_row
        assert len(_ceiling_events(ctx, exec_id)) == 1
