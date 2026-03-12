"""Contract tests for manual (user-initiated) session recovery."""

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


def _set_session_fields(db_url, session_id, **fields):
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [session_id]
    with db_conn(db_url) as conn:
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
        conn.commit()


def _get_session_row(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT id, status, recovery_attempts, agent_session_id, cwd, completed_at "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "status": row[1],
        "recovery_attempts": row[2],
        "agent_session_id": row[3],
        "cwd": row[4],
        "completed_at": row[5],
    }


def _get_execution_row(db_url, execution_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT id, status, completed_at FROM executions WHERE id = ?",
            (execution_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "status": row[1], "completed_at": row[2]}


def _get_task_queue_payload(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _worker_sync(url):
    resp = httpx.post(f"{url}/api/worker/sync", json={}, timeout=10)
    assert resp.status_code == 200
    return resp.json()


def _setup_failed_root_session(ctx, agent_type="claude_sdk"):
    """Create execution, claim lead, set to failed with agent_session_id + cwd."""
    agent_id = seed_test_agent(
        ctx["db_url"], name=f"test-{agent_type}", agent_type=agent_type
    )
    exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test prompt")
    _worker_sync(ctx["url"])
    _set_session_fields(
        ctx["db_url"],
        lead_sid,
        status="failed",
        agent_session_id="sdk-session-abc",
        cwd="/tmp/test-workspace",
        completed_at="2026-01-01 00:00:00",
    )
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "UPDATE executions SET status = 'failed', completed_at = '2026-01-01 00:00:00' WHERE id = ?",
            (exec_id,),
        )
        conn.commit()
    return agent_id, exec_id, lead_sid


def _add_child_session(
    db_url, execution_id, parent_session_id, agent_id, status="failed"
):
    """Insert a child session row directly."""
    child_id = str(uuid.uuid4())
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, "
            "cwd, agent_session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                child_id,
                execution_id,
                parent_session_id,
                agent_id,
                status,
                "child",
                "/tmp",
                "sdk-child-xyz",
            ),
        )
        conn.commit()
    return child_id


def _recover_session(url, session_id, message=None):
    body = {}
    if message is not None:
        body["message"] = message
    return httpx.post(
        f"{url}/api/sessions/{session_id}/recover",
        json=body,
        timeout=10,
    )


# === Root lead recovery ===


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_root_lead_succeeds(test_database):
    """POST /api/sessions/{id}/recover on failed root lead -> 200, session + execution recover."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["status"] == "submitted"
        assert data["execution_recovered"] is True

        session = _get_session_row(ctx["db_url"], lead_sid)
        assert session["status"] == "submitted"
        assert session["recovery_attempts"] == 0

        execution = _get_execution_row(ctx["db_url"], exec_id)
        assert execution["status"] == "submitted"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_execution_completed_at_cleared(test_database):
    """After root lead recovery, both session and execution have completed_at = NULL."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 200

        session = _get_session_row(ctx["db_url"], lead_sid)
        assert session["completed_at"] is None

        execution = _get_execution_row(ctx["db_url"], exec_id)
        assert execution["completed_at"] is None


# === Child session recovery ===


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_child_in_working_execution(test_database):
    """Recover a failed child while execution is still working -> session recovers, execution untouched."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])
        # Keep root lead as working, add a failed child
        child_id = _add_child_session(
            ctx["db_url"], exec_id, lead_sid, agent_id, status="failed"
        )

        resp = _recover_session(ctx["url"], child_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_recovered"] is False

        child = _get_session_row(ctx["db_url"], child_id)
        assert child["status"] == "submitted"

        execution = _get_execution_row(ctx["db_url"], exec_id)
        assert execution["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_child_in_failed_execution_rejected(test_database):
    """Recover a failed child when execution is failed -> 409."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)
        child_id = _add_child_session(
            ctx["db_url"], exec_id, lead_sid, agent_id, status="failed"
        )

        resp = _recover_session(ctx["url"], child_id)
        assert resp.status_code == 409
        assert "recover the root lead session first" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_child_in_completed_execution_rejected(test_database):
    """Recover a failed child when execution is completed -> 409."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])
        child_id = _add_child_session(
            ctx["db_url"], exec_id, lead_sid, agent_id, status="failed"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        resp = _recover_session(ctx["url"], child_id)
        assert resp.status_code == 409
        assert "completed execution" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_child_in_canceled_execution_rejected(test_database):
    """Recover a failed child when execution is canceled -> 409."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])
        child_id = _add_child_session(
            ctx["db_url"], exec_id, lead_sid, agent_id, status="failed"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'canceled', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        resp = _recover_session(ctx["url"], child_id)
        assert resp.status_code == 409
        assert "canceled execution" in resp.text


# === Validation tests ===


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_rejects_non_failed_session(test_database):
    """POST recover on non-failed session -> 409."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 409
        assert "must be 'failed'" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_rejects_no_session_id(test_database):
    """POST recover when agent_session_id is NULL -> 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])
        _set_session_fields(
            ctx["db_url"],
            lead_sid,
            status="failed",
            cwd="/tmp",
            completed_at="2026-01-01 00:00:00",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'failed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 400
        assert "agent session ID" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_rejects_no_cwd(test_database):
    """POST recover when cwd is NULL -> 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="test-claude", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])
        _set_session_fields(
            ctx["db_url"],
            lead_sid,
            status="failed",
            agent_session_id="sdk-abc",
            completed_at="2026-01-01 00:00:00",
        )
        # Ensure cwd is NULL
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("UPDATE sessions SET cwd = NULL WHERE id = ?", (lead_sid,))
            conn.execute(
                "UPDATE executions SET status = 'failed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 400
        assert "working directory" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_rejects_non_resumable_agent(test_database):
    """POST recover for ACP agent -> 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-acp", agent_type="acp")
        exec_id, lead_sid = create_execution_via_api(
            ctx["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx["url"])
        _set_session_fields(
            ctx["db_url"],
            lead_sid,
            status="failed",
            agent_session_id="acp-abc",
            cwd="/tmp",
            completed_at="2026-01-01 00:00:00",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'failed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 400
        assert "does not support recovery" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_rejects_deleted_agent(test_database):
    """POST recover when agent is soft-deleted -> 404."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE agents SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (agent_id,),
            )
            conn.commit()

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 404
        assert "agent not found" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_rejects_disabled_agent(test_database):
    """POST recover when agent is disabled -> 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("UPDATE agents SET enabled = false WHERE id = ?", (agent_id,))
            conn.commit()

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 400
        assert "disabled" in resp.text


# === Behavior tests ===


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_resets_counter(test_database):
    """recovery_attempts reset to 0 on manual recovery."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)
        _set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=5)

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 200

        session = _get_session_row(ctx["db_url"], lead_sid)
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_emits_events(test_database):
    """State change events emitted for session and execution on root lead recovery."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 200

        # Check session events
        session_events = httpx.get(
            f"{ctx['url']}/api/sessions/{lead_sid}/events", timeout=10
        ).json()
        recovery_events = [
            e
            for e in session_events
            if e["event_type"] == "state_change"
            and e["payload"].get("to") == "submitted"
            and e["payload"].get("manual_recovery") is True
        ]
        assert len(recovery_events) >= 1

        # Check execution events
        exec_events = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/events", timeout=10
        ).json()
        exec_recovery = [
            e
            for e in exec_events
            if e["event_type"] == "state_change"
            and e["payload"].get("to") == "submitted"
            and e["payload"].get("manual_recovery") is True
            and e.get("session_id") is None
        ]
        assert len(exec_recovery) >= 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_task_payload(test_database):
    """Task queue entry contains resumeSessionId matching agent_session_id."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        resp = _recover_session(ctx["url"], lead_sid)
        assert resp.status_code == 200

        payload = _get_task_queue_payload(ctx["db_url"], lead_sid)
        assert payload is not None
        assert payload["resumeSessionId"] == "sdk-session-abc"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_custom_message(test_database):
    """Optional message field replaces default recovery message in payload."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        resp = _recover_session(ctx["url"], lead_sid, message="Skip the failing test")
        assert resp.status_code == 200

        payload = _get_task_queue_payload(ctx["db_url"], lead_sid)
        assert payload is not None
        msg_text = payload["message"]["parts"][0]["text"]
        assert "Skip the failing test" in msg_text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_idempotent_reject(test_database):
    """Second recover call on same session (now submitted) -> 409."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        resp1 = _recover_session(ctx["url"], lead_sid)
        assert resp1.status_code == 200

        resp2 = _recover_session(ctx["url"], lead_sid)
        assert resp2.status_code == 409
        assert "must be 'failed'" in resp2.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_attempts_in_session_response(test_database):
    """GET /api/sessions/{id} includes recovery_attempts field."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)
        _set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=3)

        resp = httpx.get(f"{ctx['url']}/api/sessions/{lead_sid}", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["recovery_attempts"] == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_message_too_long(test_database):
    """Recovery message exceeding 10,000 chars -> 400."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        long_msg = "x" * 10_001
        resp = _recover_session(ctx["url"], lead_sid, message=long_msg)
        assert resp.status_code == 400
        assert "10,000" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_manual_recovery_vs_cancel_race(test_database):
    """Concurrent cancel and recover: one wins, other gets 409."""
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, exec_id, lead_sid = _setup_failed_root_session(ctx)

        # First recover succeeds
        resp1 = _recover_session(ctx["url"], lead_sid)
        assert resp1.status_code == 200

        # Session is now submitted — cancel it
        cancel_resp = httpx.post(
            f"{ctx['url']}/api/sessions/{lead_sid}/cancel", json={}, timeout=10
        )
        # Cancel should succeed (session is submitted, which is non-terminal and cancellable)
        assert cancel_resp.status_code == 200

        # Second recover attempt on the now-canceled session should fail
        # (status is 'canceled', not 'failed')
        resp2 = _recover_session(ctx["url"], lead_sid)
        assert resp2.status_code == 409
