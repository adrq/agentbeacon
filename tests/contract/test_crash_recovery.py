"""Contract tests for crash recovery (Tier 2): restart-and-resume."""

import json
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

SHORT_GRACE = {"AGENTBEACON_RECOVERY_GRACE_SECS": "1"}

# Scheduler clamps grace to max(3) internally. Wait past that + margin
# so negative tests actually prove recovery was skipped by predicate.
NEGATIVE_WAIT = 5


def _wait_for_recovery(db_url, session_id, timeout=5, interval=0.15):
    """Poll until recovery scan mutates session state or attempts counter."""
    baseline = _get_session(db_url, session_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _get_session(db_url, session_id)
        if (
            current["status"] != baseline["status"]
            or current["recovery_attempts"] != baseline["recovery_attempts"]
        ):
            return current
        time.sleep(interval)
    return _get_session(db_url, session_id)


def _worker_sync(url, payload=None, timeout=10):
    """POST /api/worker/sync with optional JSON body."""
    resp = httpx.post(f"{url}/api/worker/sync", json=payload or {}, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _set_session_fields(db_url, session_id, **fields):
    """Update session fields via direct SQL."""
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [session_id]
    with db_conn(db_url) as conn:
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
        conn.commit()


def _get_session(db_url, session_id):
    """Fetch a session row as a dict."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT id, status, recovery_attempts, agent_session_id, cwd FROM sessions WHERE id = ?",
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
    }


def _get_task_queue_payload(db_url, session_id):
    """Fetch the task_payload from task_queue for a given session_id."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _wait_for_task_payload(db_url, session_id, timeout=5, interval=0.15):
    """Poll until task_queue has a payload for the session."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = _get_task_queue_payload(db_url, session_id)
        if payload is not None:
            return payload
        time.sleep(interval)
    return None


def _backdate_session(db_url, session_id):
    """Backdate session updated_at so recovery scan sees it as clearly stale.

    SQLite CURRENT_TIMESTAMP has second precision, so without backdating,
    a session updated in the same second as scheduler startup would be missed
    by the `updated_at < startup_time` filter.
    """
    with db_conn(db_url) as conn:
        # Works for both SQLite (datetime function) and PostgreSQL (interval)
        if db_url.startswith("postgres"):
            conn.execute(
                "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP - INTERVAL '10 seconds' WHERE id = ?",
                (session_id,),
            )
        else:
            conn.execute(
                "UPDATE sessions SET updated_at = datetime('now', '-10 seconds') WHERE id = ?",
                (session_id,),
            )
        conn.commit()


def _setup_working_session(ctx, agent_type="claude_sdk"):
    """Create execution + claim lead → working, set agent_session_id + cwd."""
    agent_id = seed_test_agent(
        ctx["db_url"], name=f"test-{agent_type}", agent_type=agent_type
    )
    exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test prompt")
    # Claim lead → working
    _worker_sync(ctx["url"])
    # Set agent_session_id and cwd
    _set_session_fields(
        ctx["db_url"],
        lead_sid,
        agent_session_id="sdk-session-abc",
        cwd="/tmp/test-workspace",
    )
    # Backdate so the session is clearly older than any future scheduler startup
    _backdate_session(ctx["db_url"], lead_sid)
    return agent_id, exec_id, lead_sid


# --- Tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_resubmits_working_session(test_database):
    """Working session with agent_session_id is resubmitted after grace period."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    # Restart scheduler against same DB
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        session = _wait_for_recovery(ctx2["db_url"], lead_sid)
        assert session["status"] == "submitted"
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_resubmits_input_required_session(test_database):
    """input-required session with agent_session_id is resubmitted."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)
        _set_session_fields(ctx1["db_url"], lead_sid, status="input-required")

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        session = _wait_for_recovery(ctx2["db_url"], lead_sid)
        assert session["status"] == "submitted"
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_session_without_agent_session_id(test_database):
    """Session without agent_session_id is NOT recovered."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        agent_id = seed_test_agent(
            ctx1["db_url"], name="claude-no-sid", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx1["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx1["url"])
        # Set cwd but NOT agent_session_id
        _set_session_fields(ctx1["db_url"], lead_sid, cwd="/tmp/workspace")

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        time.sleep(NEGATIVE_WAIT)
        session = _get_session(ctx2["db_url"], lead_sid)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_acp_sessions(test_database):
    """ACP agent type sessions are NOT recovered."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1, agent_type="acp")

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        time.sleep(NEGATIVE_WAIT)
        session = _get_session(ctx2["db_url"], lead_sid)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_budget_exhausted(test_database):
    """Session with recovery_attempts >= max is permanently failed."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)
        _set_session_fields(ctx1["db_url"], lead_sid, recovery_attempts=2)

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        session = _wait_for_recovery(ctx2["db_url"], lead_sid)
        assert session["status"] == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_terminal_execution(test_database):
    """Session in a completed execution is NOT recovered."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, exec_id, lead_sid = _setup_working_session(ctx1)
        # Mark execution as completed
        with db_conn(ctx1["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        time.sleep(NEGATIVE_WAIT)
        session = _get_session(ctx2["db_url"], lead_sid)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_task_payload_has_resume_session_id(test_database):
    """Recovery task_payload contains resumeSessionId matching stored agent_session_id."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        _wait_for_recovery(ctx2["db_url"], lead_sid)
        payload = _wait_for_task_payload(ctx2["db_url"], lead_sid)
        assert payload is not None, "no task_payload found in queue"
        assert payload["resumeSessionId"] == "sdk-session-abc"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_preserves_driver_and_config(test_database):
    """Recovery task_payload has correct driver.platform and agent_config."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        _wait_for_recovery(ctx2["db_url"], lead_sid)
        payload = _wait_for_task_payload(ctx2["db_url"], lead_sid)
        assert payload is not None
        assert payload["driver"]["platform"] == "claude_sdk"
        assert "agent_config" in payload


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_increments_counter(test_database):
    """recovery_attempts is incremented from 0 to 1."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        session = _wait_for_recovery(ctx2["db_url"], lead_sid)
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_state_change_event_logged(test_database):
    """State change event is logged for recovery transition."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, exec_id, lead_sid = _setup_working_session(ctx1)

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        _wait_for_recovery(ctx2["db_url"], lead_sid)
        with db_conn(ctx2["db_url"]) as conn:
            row = conn.execute(
                "SELECT payload FROM events WHERE execution_id = ? AND event_type = 'state_change' AND session_id = ? ORDER BY id DESC LIMIT 1",
                (exec_id, lead_sid),
            ).fetchone()
        assert row is not None, "no state_change event found"
        payload = json.loads(row[0])
        assert payload["from"] == "working"
        assert payload["to"] == "submitted"
        assert payload["recovery_attempt"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_double_restart_recovers_again(test_database):
    """Second restart recovers same session (within budget)."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    # First restart
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        session = _wait_for_recovery(ctx2["db_url"], lead_sid)
        assert session["status"] == "submitted"
        assert session["recovery_attempts"] == 1
        # Simulate worker claiming and working again
        _set_session_fields(
            ctx2["db_url"],
            lead_sid,
            status="working",
            agent_session_id="sdk-session-def",
            cwd="/tmp/test-workspace",
        )

    # Second restart
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx3:
        _wait_for_recovery(ctx3["db_url"], lead_sid)
        session = _get_session(ctx3["db_url"], lead_sid)
        assert session["status"] == "submitted"
        assert session["recovery_attempts"] == 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_uses_session_cwd(test_database):
    """Recovery task_payload cwd matches stored session.cwd."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        _wait_for_recovery(ctx2["db_url"], lead_sid)
        payload = _wait_for_task_payload(ctx2["db_url"], lead_sid)
        assert payload is not None
        assert payload["cwd"] == "/tmp/test-workspace"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_skips_session_without_cwd(test_database):
    """Session with cwd = NULL is NOT recovered (stays in original state)."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        agent_id = seed_test_agent(
            ctx1["db_url"], name="claude-no-cwd", agent_type="claude_sdk"
        )
        exec_id, lead_sid = create_execution_via_api(
            ctx1["url"], agent_id, "test prompt"
        )
        _worker_sync(ctx1["url"])
        _set_session_fields(
            ctx1["db_url"],
            lead_sid,
            agent_session_id="sdk-session-abc",
            cwd=None,
        )

    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        time.sleep(NEGATIVE_WAIT)
        session = _get_session(ctx2["db_url"], lead_sid)
        # cwd IS NULL is filtered by find_recoverable, so session stays working
        # (the SQL WHERE clause has cwd IS NOT NULL)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_heartbeat_prevents_recovery(test_database):
    """Session synced during grace period is NOT recovered."""
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        _agent_id, _exec_id, lead_sid = _setup_working_session(ctx1)

    # Start second scheduler — sync during grace period to update updated_at.
    # The heartbeat pushes updated_at past startup_time, so the session is
    # excluded from the recovery scan's `updated_at < startup_time` filter.
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        _worker_sync(
            ctx2["url"],
            payload={"sessionState": {"sessionId": lead_sid, "status": "running"}},
        )
        time.sleep(NEGATIVE_WAIT)
        session = _get_session(ctx2["db_url"], lead_sid)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0
