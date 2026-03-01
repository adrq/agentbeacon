"""Contract tests for execution complete endpoint (POST /api/executions/{id}/complete)."""

import json
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


def _set_execution_status(ctx, exec_id, status):
    """Set execution status directly in DB."""
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "UPDATE executions SET status = ? WHERE id = ?",
            (status, exec_id),
        )
        conn.commit()


def _set_session_status(ctx, session_id, status):
    """Set session status directly in DB."""
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "UPDATE sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
        conn.commit()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_from_input_required(test_database):
    """Complete from input-required: happy path."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "input-required")
        _set_session_status(ctx, lead_sid, "input-required")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 200

        body = resp.json()
        assert body["execution"]["status"] == "completed"
        assert body["execution"]["completed_at"] is not None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_from_working(test_database):
    """Complete from working: root session becomes canceled (interrupted)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "working")
        _set_session_status(ctx, lead_sid, "working")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 200

        body = resp.json()
        assert body["execution"]["status"] == "completed"
        assert body["execution"]["completed_at"] is not None

        with db_conn(ctx["db_url"]) as conn:
            lead_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (lead_sid,)
            ).fetchone()[0]

        # Working root session becomes canceled (interrupted) per Release mode
        assert lead_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_terminal_rejects(test_database):
    """409 for already-terminal executions."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")

        for terminal_status in ["completed", "failed", "canceled"]:
            exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
            _set_execution_status(ctx, exec_id, terminal_status)

            resp = httpx.post(
                f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10
            )
            assert resp.status_code == 409, (
                f"Expected 409 for {terminal_status}, got {resp.status_code}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_submitted_rejects(test_database):
    """409 for submitted execution (nothing to complete)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, _lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        # Execution starts as submitted by default

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_cascade_release_mode(test_database):
    """Release mode: IR→completed, working→canceled, submitted→canceled, completed→unchanged."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "input-required")
        _set_session_status(ctx, lead_sid, "input-required")

        child_ir = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        child_working = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        child_submitted = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="submitted"
        )
        child_completed = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="completed"
        )

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            statuses = {}
            for sid in [
                lead_sid,
                child_ir,
                child_working,
                child_submitted,
                child_completed,
            ]:
                row = conn.execute(
                    "SELECT status FROM sessions WHERE id = ?", (sid,)
                ).fetchone()
                statuses[sid] = row[0]

        assert statuses[lead_sid] == "completed"
        assert statuses[child_ir] == "completed"
        assert statuses[child_working] == "canceled"
        assert statuses[child_submitted] == "canceled"
        assert statuses[child_completed] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_with_deep_tree(test_database):
    """Multi-level cascade: grandchild and great-grandchild get Release-mode transitions."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "input-required")
        _set_session_status(ctx, lead_sid, "input-required")

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        grandchild = _create_child_session(
            ctx, child, exec_id, agent_id, status="working"
        )
        great_grandchild = _create_child_session(
            ctx, grandchild, exec_id, agent_id, status="input-required"
        )

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            statuses = {}
            for sid in [lead_sid, child, grandchild, great_grandchild]:
                row = conn.execute(
                    "SELECT status FROM sessions WHERE id = ?", (sid,)
                ).fetchone()
                statuses[sid] = row[0]

        assert statuses[lead_sid] == "completed"
        assert statuses[child] == "completed"
        assert statuses[grandchild] == "canceled"  # working → canceled
        assert statuses[great_grandchild] == "completed"  # IR → completed


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_emits_state_change_events(test_database):
    """Execution-level state_change event emitted with correct payload."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "input-required")
        _set_session_status(ctx, lead_sid, "input-required")

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 200

        events_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/events", timeout=10
        )
        events = events_resp.json()

        # Find the execution-level state_change event (session_id is null)
        exec_state_events = [
            e
            for e in events
            if e["event_type"] == "state_change" and e.get("session_id") is None
        ]
        assert len(exec_state_events) >= 1

        # payload is already parsed JSON in the API response
        payload = exec_state_events[-1]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["from"] == "input-required"
        assert payload["to"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_idempotent_after_completion(test_database):
    """Second complete returns 409 (execution is already terminal)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "input-required")
        _set_session_status(ctx, lead_sid, "input-required")

        resp1 = httpx.post(
            f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10
        )
        assert resp1.status_code == 200

        resp2 = httpx.post(
            f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10
        )
        assert resp2.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_not_found(test_database):
    """404 for nonexistent execution."""
    with scheduler_context(db_url=test_database) as ctx:
        seed_test_agent(ctx["db_url"], name="lead-agent")
        fake_id = str(uuid.uuid4())

        resp = httpx.post(f"{ctx['url']}/api/executions/{fake_id}/complete", timeout=10)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_complete_no_sessions(test_database):
    """Execution with no sessions (manually deleted) still completes."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")
        _set_execution_status(ctx, exec_id, "input-required")

        # Delete all sessions
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM sessions WHERE execution_id = ?", (exec_id,))
            conn.commit()

        resp = httpx.post(f"{ctx['url']}/api/executions/{exec_id}/complete", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["execution"]["status"] == "completed"
