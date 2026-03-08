"""Contract tests for runtime liveness detection (periodic staleness scan)."""

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

SHORT_LIVENESS = {
    "AGENTBEACON_LIVENESS_INTERVAL_SECS": "60",  # minimum allowed (floor)
    "AGENTBEACON_RECOVERY_GRACE_SECS": "1",  # clamped to 3s by scheduler
}


def _worker_sync(url, payload=None, timeout=10):
    resp = httpx.post(f"{url}/api/worker/sync", json=payload or {}, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _set_session_fields(db_url, session_id, **fields):
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [session_id]
    with db_conn(db_url) as conn:
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
        conn.commit()


def _get_session(db_url, session_id):
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


def _backdate_session(db_url, session_id, seconds=120):
    """Backdate session updated_at so liveness scan sees it as clearly stale."""
    with db_conn(db_url) as conn:
        if db_url.startswith("postgres"):
            conn.execute(
                f"UPDATE sessions SET updated_at = CURRENT_TIMESTAMP - INTERVAL '{seconds} seconds' WHERE id = ?",
                (session_id,),
            )
        else:
            conn.execute(
                f"UPDATE sessions SET updated_at = datetime('now', '-{seconds} seconds') WHERE id = ?",
                (session_id,),
            )
        conn.commit()


def _wait_for_status_change(db_url, session_id, timeout=45, interval=0.2):
    """Poll until the session status changes from its current value."""
    baseline = _get_session(db_url, session_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _get_session(db_url, session_id)
        if current["status"] != baseline["status"]:
            return current
        time.sleep(interval)
    return _get_session(db_url, session_id)


def _setup_working_session(ctx, agent_type="claude_sdk"):
    """Create execution + claim lead -> working, set agent_session_id + cwd."""
    agent_id = seed_test_agent(
        ctx["db_url"], name=f"test-{agent_type}", agent_type=agent_type
    )
    exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test prompt")
    _worker_sync(ctx["url"])
    _set_session_fields(
        ctx["db_url"],
        lead_sid,
        agent_session_id="sdk-session-abc",
        cwd="/tmp/test-workspace",
    )
    return agent_id, exec_id, lead_sid


# --- Tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_recovers_stale_sdk_session(test_database):
    """Stale claude_sdk session is recovered to submitted by periodic scan."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx)
        _backdate_session(ctx["db_url"], lead_sid)

        session = _wait_for_status_change(ctx["db_url"], lead_sid)
        assert session["status"] == "submitted"
        assert session["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_fails_stale_acp_session(test_database):
    """Stale ACP session is permanently failed (not resubmitted)."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx, agent_type="acp")
        _backdate_session(ctx["db_url"], lead_sid)

        session = _wait_for_status_change(ctx["db_url"], lead_sid)
        assert session["status"] == "failed"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_skips_fresh_session(test_database):
    """Session with recent heartbeat is NOT touched by scan."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx)
        # Do NOT backdate — session has fresh updated_at

        # Wait past grace period (clamped to 3s) but well before first periodic
        # scan (30s) to verify fresh session is untouched by initial scan
        time.sleep(8)
        session = _get_session(ctx["db_url"], lead_sid)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_emits_state_change_event(test_database):
    """Recovery via liveness scan emits state_change event with recovery_attempt."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx)
        _backdate_session(ctx["db_url"], lead_sid)

        _wait_for_status_change(ctx["db_url"], lead_sid)
        with db_conn(ctx["db_url"]) as conn:
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
def test_liveness_acp_failure_cascades_children(test_database):
    """Failed ACP root session cascades children to canceled."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        # Create ACP root session
        agent_id, exec_id, lead_sid = _setup_working_session(ctx, agent_type="acp")

        # Create a child session manually
        child_agent_id = seed_test_agent(
            ctx["db_url"], name="child-claude", agent_type="claude_sdk"
        )
        child_sid = "child-" + lead_sid[:20]
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug) VALUES (?, ?, ?, ?, 'working', 'child')",
                (child_sid, exec_id, lead_sid, child_agent_id),
            )
            conn.commit()

        _backdate_session(ctx["db_url"], lead_sid)

        # Wait for root to be failed
        _wait_for_status_change(ctx["db_url"], lead_sid)
        root = _get_session(ctx["db_url"], lead_sid)
        assert root["status"] == "failed"

        # Give cascade a moment to propagate
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            child = _get_session(ctx["db_url"], child_sid)
            if child["status"] == "canceled":
                break
            time.sleep(0.2)
        child = _get_session(ctx["db_url"], child_sid)
        assert child["status"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_acp_failure_records_event(test_database):
    """ACP failure emits state_change event with error_kind non_resumable_stale."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx, agent_type="acp")
        _backdate_session(ctx["db_url"], lead_sid)

        _wait_for_status_change(ctx["db_url"], lead_sid)
        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT payload FROM events WHERE execution_id = ? AND event_type = 'state_change' AND session_id = ? ORDER BY id DESC LIMIT 1",
                (exec_id, lead_sid),
            ).fetchone()
        assert row is not None, "no state_change event found"
        payload = json.loads(row[0])
        assert payload["from"] == "working"
        assert payload["to"] == "failed"
        assert payload["error_kind"] == "non_resumable_stale"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_respects_budget(test_database):
    """Session at max recovery attempts is permanently failed (not resubmitted)."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx)
        _set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=2)
        _backdate_session(ctx["db_url"], lead_sid)

        session = _wait_for_status_change(ctx["db_url"], lead_sid)
        assert session["status"] == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_liveness_skips_terminal_execution(test_database):
    """Stale session in completed execution is NOT recovered."""
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id, exec_id, lead_sid = _setup_working_session(ctx)
        # Mark execution as completed
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (exec_id,),
            )
            conn.commit()
        _backdate_session(ctx["db_url"], lead_sid)

        # Wait past grace period (clamped to 3s) but well before first periodic
        # scan (30s) — execution is terminal, so both find_recoverable and
        # find_stale_non_resumable filter it out via execution status check
        time.sleep(8)
        session = _get_session(ctx["db_url"], lead_sid)
        assert session["status"] == "working"
        assert session["recovery_attempts"] == 0
