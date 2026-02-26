"""Contract tests for session cancel/complete REST endpoints."""

import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


def _create_child_session(ctx, lead_session_id, exec_id, agent_id, status="submitted"):
    """Insert a child session directly into the DB."""
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (child_id, exec_id, lead_session_id, agent_id, status),
        )
        conn.commit()
    return child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_session_cascades(test_database):
    """POST /api/sessions/{id}/cancel cascades to children."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        gc_id = _create_child_session(
            ctx, child_id, exec_id, agent_id, status="submitted"
        )

        # Cancel the child (not the lead)
        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/cancel", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["canceled"] is True
        assert body["sessions_terminated"] == 2  # child + grandchild

        with db_conn(ctx["db_url"]) as conn:
            child_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()[0]
            gc_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_id,)
            ).fetchone()[0]

        assert child_status == "canceled"
        assert gc_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_session_terminal_rejects(test_database):
    """Cannot cancel already-terminal session."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="completed"
        )

        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/cancel", timeout=10)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_complete_session_requires_input_required(test_database):
    """POST /api/sessions/{id}/complete requires input-required state."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/complete", timeout=10)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_complete_session_cascades(test_database):
    """Session complete cascades to children using Release mode."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        gc_ir = _create_child_session(
            ctx, child_id, exec_id, agent_id, status="input-required"
        )
        gc_working = _create_child_session(
            ctx, child_id, exec_id, agent_id, status="working"
        )

        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/complete", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["completed"] is True

        with db_conn(ctx["db_url"]) as conn:
            child_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()[0]
            gc_ir_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_ir,)
            ).fetchone()[0]
            gc_working_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_working,)
            ).fetchone()[0]

        # Release mode: input-required→completed, working→canceled
        assert child_status == "completed"
        assert gc_ir_status == "completed"
        assert gc_working_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_session_notifies_parent(test_database):
    """Parent receives notification when child is canceled by user."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/cancel", timeout=10)
        assert resp.status_code == 200

        # Check that a task was pushed to the parent's inbox
        # Note: first task_queue entry is the bootstrap task from execution creation,
        # so we check all entries for the notification string.
        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchall()

        payloads = [r[0] for r in rows]
        notification = next(
            (p for p in payloads if "canceled" in p and child_id in p), None
        )
        assert notification is not None, (
            f"No notification with 'canceled' and child_id found in {payloads}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_complete_session_notifies_parent(test_database):
    """Parent receives notification when child is completed by user."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )

        resp = httpx.post(f"{ctx['url']}/api/sessions/{child_id}/complete", timeout=10)
        assert resp.status_code == 200

        # Check that a task was pushed to the parent's inbox
        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchall()

        payloads = [r[0] for r in rows]
        notification = next(
            (p for p in payloads if "completed" in p and child_id in p), None
        )
        assert notification is not None, (
            f"No notification with 'completed' and child_id found in {payloads}"
        )
