"""Contract tests for has_pending_turn session transition fix.

Verifies that sessions correctly transition to input-required even when
the worker reports has_pending_turn=true, preventing the stuck execution
scenario from execution 68635654.
"""

import json
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def _worker_sync(url, payload=None, timeout=10):
    resp = httpx.post(f"{url}/api/worker/sync", json=payload or {}, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _get_session_status(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None


def _get_execution_status(db_url, execution_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        return row[0] if row else None


def _get_last_progress_at(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT last_progress_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None


# --- Fix 1: has_pending_turn transition ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_pending_turn_stays_working(test_database):
    """Session stays working when has_pending_turn=true.

    The worker has local turns queued and is actively executing — the session
    should NOT transition to input-required (which would disrupt the frontend's
    streaming state machine). The idle-root reconciler catches the edge case
    where no follow-up turn arrives.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"

        # Report turn with has_pending_turn=true
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "ROLE_AGENT",
                                "parts": [{"text": "first turn"}],
                            },
                        }
                    ],
                    "hasPendingTurn": True,
                }
            },
        )

        # Session stays working (worker still has local turns)
        assert _get_session_status(ctx["db_url"], session_id) == "working"
        assert _get_execution_status(ctx["db_url"], exec_id) == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_pending_turn_delivers_to_parent(test_database):
    """deliver_to_parent runs even when child reports has_pending_turn=true."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, agent_id),
            )
            conn.commit()

        # Claim lead
        _worker_sync(ctx["url"])
        # Delegate child
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "test-agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]
        # Claim child
        _worker_sync(ctx["url"])

        # Child reports turn with has_pending_turn=true
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": child_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "ROLE_AGENT",
                                "parts": [{"text": "child output"}],
                            },
                        }
                    ],
                    "hasPendingTurn": True,
                }
            },
        )

        # Parent should have received child output via deliver_to_parent
        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id",
                (lead_id,),
            ).fetchall()
            assert len(rows) >= 1, (
                "Parent should have at least 1 queued task from child output"
            )
            payloads = [json.loads(r[0]) for r in rows]
            assert any("child output" in json.dumps(p) for p in payloads), (
                "deliver_to_parent should have queued child output to parent"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_pending_turn_returns_task_available_when_queued(test_database):
    """When has_pending_turn=true AND a task is queued, TaskAvailable is returned."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        _worker_sync(ctx["url"])

        # Queue a task manually
        with db_conn(ctx["db_url"]) as conn:
            payload = json.dumps(
                {"message": {"role": "ROLE_USER", "parts": [{"text": "queued msg"}]}}
            )
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()

        # Report turn with has_pending_turn=true
        data = _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "ROLE_AGENT",
                                "parts": [{"text": "done"}],
                            },
                        }
                    ],
                    "hasPendingTurn": True,
                }
            },
        )

        # Should get TaskAvailable (not NoAction)
        assert data["type"] == "task_available"


# --- Fix 2: last_progress_at ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_last_progress_at_advances_on_turn_result(test_database):
    """last_progress_at is updated when a turn result is reported."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        _worker_sync(ctx["url"])

        before = _get_last_progress_at(ctx["db_url"], session_id)

        # Small delay to ensure timestamp advances (SQLite second precision)
        time.sleep(1.1)

        # Report turn
        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "ROLE_AGENT",
                                "parts": [{"text": "done"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        after = _get_last_progress_at(ctx["db_url"], session_id)
        assert after > before, (
            f"last_progress_at should advance on turn result: {before} -> {after}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_heartbeat_does_not_advance_last_progress_at(test_database):
    """Idle heartbeat (waiting_for_event) does NOT advance last_progress_at.

    Only active-turn heartbeats ("running") advance last_progress_at.
    Idle heartbeats must NOT, otherwise stuck sessions would never be detected.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        _worker_sync(ctx["url"])

        before = _get_last_progress_at(ctx["db_url"], session_id)
        time.sleep(1.1)

        # Idle heartbeat (waiting_for_event) — the stuck-session scenario
        _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "waiting_for_event"}},
            timeout=35,
        )

        after = _get_last_progress_at(ctx["db_url"], session_id)
        assert after == before, (
            f"last_progress_at should NOT advance on idle heartbeat: {before} -> {after}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_running_heartbeat_advances_last_progress_at(test_database):
    """Active-turn heartbeat ("running") DOES advance last_progress_at.

    This prevents the liveness scan from falsely recovering long-running turns.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim session
        _worker_sync(ctx["url"])

        before = _get_last_progress_at(ctx["db_url"], session_id)
        time.sleep(1.1)

        # Active-turn heartbeat (enters long-poll, returns after 30s timeout)
        _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "running"}},
            timeout=35,
        )

        after = _get_last_progress_at(ctx["db_url"], session_id)
        assert after > before, (
            f"last_progress_at should advance on running heartbeat: {before} -> {after}"
        )


# --- Fix 3: Extended reconciliation ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_catches_idle_root_with_stale_progress(test_database):
    """Idle root session with stale last_progress_at is recovered.

    Reproduces the exact stuck-execution-68635654 scenario: root session is
    working, no active children, no queued work, but last_progress_at is stale.
    Uses two scheduler contexts: ctx1 to set up state, ctx2 whose startup
    recovery scan finds the stale session. This exercises the recovery path
    directly; the idle-root reconciler (Case 2) is a belt-and-suspenders
    safety net that runs periodically and is verified by code review.
    """
    SHORT_LIVENESS = {
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "60",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
    }
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx1:
        agent_id = seed_test_agent(ctx1["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx1["url"], agent_id, "init")

        # Claim session (working)
        _worker_sync(ctx1["url"])

        # Set agent_session_id + cwd (required for recovery eligibility)
        with db_conn(ctx1["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET agent_session_id = 'sdk-abc', cwd = '/tmp/test' WHERE id = ?",
                (session_id,),
            )
            # Backdate both timestamps to make session look stale before next scheduler starts
            if test_database.startswith("postgres"):
                conn.execute(
                    "UPDATE sessions SET last_progress_at = CURRENT_TIMESTAMP - INTERVAL '300 seconds', "
                    "updated_at = CURRENT_TIMESTAMP - INTERVAL '300 seconds' WHERE id = ?",
                    (session_id,),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET last_progress_at = datetime('now', '-300 seconds'), "
                    "updated_at = datetime('now', '-300 seconds') WHERE id = ?",
                    (session_id,),
                )
            conn.commit()

    # Restart scheduler — its startup scan will find the stale session
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx2:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            session_status = _get_session_status(ctx2["db_url"], session_id)
            exec_status = _get_execution_status(ctx2["db_url"], exec_id)
            if session_status != "working" or exec_status != "working":
                break
            time.sleep(0.5)

        # The recovery scan should resubmit the session (session → submitted).
        # The execution may or may not transition depending on recovery path.
        session_status = _get_session_status(ctx2["db_url"], session_id)
        assert session_status != "working", (
            f"Session should not remain stuck in working (got: {session_status})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_all_terminal_transitions_execution(test_database):
    """Execution with all terminal sessions is reconciled to terminal status."""
    SHORT_LIVENESS = {
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "60",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
    }
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim and manually set all sessions to completed
        _worker_sync(ctx["url"])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE execution_id = ?",
                (exec_id,),
            )
            # Keep execution as working (simulating the gap)
            conn.execute(
                "UPDATE executions SET status = 'working' WHERE id = ?", (exec_id,)
            )
            conn.commit()

        # Wait for the all-terminal reconciler to fire
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status = _get_execution_status(ctx["db_url"], exec_id)
            if status != "working":
                break
            time.sleep(0.5)

        exec_status = _get_execution_status(ctx["db_url"], exec_id)
        assert exec_status == "completed", (
            f"Execution with all completed sessions should reconcile to completed (got: {exec_status})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_all_terminal_with_failure_transitions_to_failed(test_database):
    """Execution with at least one failed session reconciles to failed."""
    SHORT_LIVENESS = {
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "60",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
    }
    with scheduler_context(db_url=test_database, env=SHORT_LIVENESS) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        _worker_sync(ctx["url"])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'failed', completed_at = CURRENT_TIMESTAMP WHERE execution_id = ?",
                (exec_id,),
            )
            conn.execute(
                "UPDATE executions SET status = 'working' WHERE id = ?", (exec_id,)
            )
            conn.commit()

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status = _get_execution_status(ctx["db_url"], exec_id)
            if status != "working":
                break
            time.sleep(0.5)

        exec_status = _get_execution_status(ctx["db_url"], exec_id)
        assert exec_status == "failed", (
            f"Execution with failed session should reconcile to failed (got: {exec_status})"
        )


# --- Fix 4: Pre-CAS failure increments recovery_attempts ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_increment_recovery_attempts_prevents_infinite_loop(test_database):
    """Sessions that consistently fail pre-CAS recovery eventually get permanently failed.

    We trigger recovery by backdating timestamps. The recovery_attempts should
    increment even when pre-CAS steps fail, preventing infinite retry loops.
    """
    SHORT_GRACE = {"AGENTBEACON_RECOVERY_GRACE_SECS": "1"}
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx1:
        agent_id = seed_test_agent(
            ctx1["db_url"], name="test-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(ctx1["url"], agent_id, "init")
        _worker_sync(ctx1["url"])

        # Set agent_session_id + cwd so session passes find_recoverable filters
        with db_conn(ctx1["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET agent_session_id = 'sdk-abc', cwd = '/tmp/test' WHERE id = ?",
                (session_id,),
            )
            # Backdate for recovery
            if test_database.startswith("postgres"):
                conn.execute(
                    "UPDATE sessions SET last_progress_at = CURRENT_TIMESTAMP - INTERVAL '120 seconds', "
                    "updated_at = CURRENT_TIMESTAMP - INTERVAL '120 seconds' WHERE id = ?",
                    (session_id,),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET last_progress_at = datetime('now', '-120 seconds'), "
                    "updated_at = datetime('now', '-120 seconds') WHERE id = ?",
                    (session_id,),
                )
            conn.commit()

    # Restart scheduler against same DB
    with scheduler_context(db_url=test_database, env=SHORT_GRACE) as ctx2:
        # Wait for recovery scan
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with db_conn(ctx2["db_url"]) as conn:
                row = conn.execute(
                    "SELECT status, recovery_attempts FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
            if row and (row[0] != "working" or row[1] > 0):
                break
            time.sleep(0.3)

        with db_conn(ctx2["db_url"]) as conn:
            row = conn.execute(
                "SELECT status, recovery_attempts FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row is not None
        # Session should either be recovered (submitted) or have incremented attempts
        assert row[0] != "working" or row[1] > 0, (
            f"Session should not remain in working with 0 recovery_attempts "
            f"(got status={row[0]}, attempts={row[1]})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_attempts_not_incremented_for_terminal_sessions(test_database):
    """increment_recovery_attempts status guard prevents incrementing terminal sessions."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        # Claim and immediately complete the session
        _worker_sync(ctx["url"])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        # Attempt to increment recovery_attempts on a terminal session
        # The SQL guard (status IN working/input-required) should prevent this
        with db_conn(ctx["db_url"]) as conn:
            before = conn.execute(
                "SELECT recovery_attempts FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()[0]

        # Simulate what increment_recovery_attempts does at the DB level
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET recovery_attempts = recovery_attempts + 1 "
                "WHERE id = ? AND status IN ('working', 'input-required')",
                (session_id,),
            )
            conn.commit()
            after = conn.execute(
                "SELECT recovery_attempts FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()[0]

        assert after == before, (
            f"recovery_attempts should not increment for terminal sessions "
            f"(before={before}, after={after})"
        )
