"""Regression tests for refactored execution cancel (uses terminate_subtree)."""

import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


def _create_child_session(ctx, parent_id, exec_id, agent_id, status="submitted"):
    """Insert a child session directly into the DB."""
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (child_id, exec_id, parent_id, agent_id, status),
        )
        conn.commit()
    return child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_cancel_terminates_all_sessions(test_database):
    """Execution cancel cascades via terminate_subtree."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child1 = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        child2 = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            lead_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (lead_sid,)
            ).fetchone()[0]
            c1_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child1,)
            ).fetchone()[0]
            c2_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child2,)
            ).fetchone()[0]
            exec_status = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]

        assert lead_status == "canceled"
        assert c1_status == "canceled"
        assert c2_status == "canceled"
        assert exec_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_cancel_with_deep_tree(test_database):
    """Execution cancel works with multi-level session tree."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        grandchild = _create_child_session(
            ctx, child, exec_id, agent_id, status="submitted"
        )
        great_grandchild = _create_child_session(
            ctx, grandchild, exec_id, agent_id, status="input-required"
        )

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=10)
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            statuses = {}
            for sid in [lead_sid, child, grandchild, great_grandchild]:
                row = conn.execute(
                    "SELECT status FROM sessions WHERE id = ?", (sid,)
                ).fetchone()
                statuses[sid] = row[0]

        for sid, status in statuses.items():
            assert status == "canceled", (
                f"Session {sid} should be canceled, got {status}"
            )
