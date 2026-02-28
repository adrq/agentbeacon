"""Contract tests for crash detection, cascade, and parent notification."""

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


def _worker_sync(url, payload=None, timeout=10):
    """POST /api/worker/sync with optional JSON body."""
    resp = httpx.post(f"{url}/api/worker/sync", json=payload or {}, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _report_crash(
    url,
    session_id,
    error="process died (exit code: 1)",
    stderr="segfault",
    error_kind="executor_failed",
):
    """Simulate worker reporting a crash via /api/worker/sync."""
    result_payload = {
        "sessionId": session_id,
        "error": error,
        "stderr": stderr,
    }
    if error_kind is not None:
        result_payload["errorKind"] = error_kind
    resp = httpx.post(
        f"{url}/api/worker/sync",
        json={"sessionResult": result_payload},
        timeout=10,
    )
    assert resp.status_code == 200
    return resp.json()


def _create_child_session(ctx, parent_id, exec_id, agent_id, status="working"):
    """Insert a child session directly into the DB."""
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (child_id, exec_id, parent_id, agent_id, status),
        )
        conn.commit()
    return child_id


# --- Tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_marks_session_failed(test_database):
    """Crash report transitions session to failed with completed_at set."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        # Claim lead → working
        _worker_sync(ctx["url"])

        _report_crash(ctx["url"], lead_sid)

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status, completed_at FROM sessions WHERE id = ?",
                (lead_sid,),
            ).fetchone()

        assert row[0] == "failed"
        assert row[1] is not None, "completed_at should be set on failed session"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_state_change_event_includes_error_context(test_database):
    """State_change event for crash includes error and stderr."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        _report_crash(
            ctx["url"],
            lead_sid,
            error="broken pipe",
            stderr="SIGSEGV at 0xdeadbeef",
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchall()

        # Find the state_change to "failed"
        failed_events = []
        for (payload_str,) in rows:
            payload = json.loads(payload_str)
            if payload.get("to") == "failed":
                failed_events.append(payload)

        assert len(failed_events) == 1
        ev = failed_events[0]
        assert ev["from"] == "working"
        assert ev["error"] == "broken pipe"
        assert ev["stderr"] == "SIGSEGV at 0xdeadbeef"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_cascades_children_to_canceled(test_database):
    """Crash on parent cascades all non-terminal children to canceled."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child1 = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        child2 = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )

        _report_crash(ctx["url"], lead_sid)

        with db_conn(ctx["db_url"]) as conn:
            c1_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child1,)
            ).fetchone()[0]
            c2_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child2,)
            ).fetchone()[0]

        assert c1_status == "canceled"
        assert c2_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_cascade_skips_terminal_children(test_database):
    """Crash cascade skips already-terminal children."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child_working = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        child_completed = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="completed"
        )
        child_failed = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="failed"
        )

        _report_crash(ctx["url"], lead_sid)

        with db_conn(ctx["db_url"]) as conn:
            w_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_working,)
            ).fetchone()[0]
            c_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_completed,)
            ).fetchone()[0]
            f_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_failed,)
            ).fetchone()[0]

        assert w_status == "canceled"
        assert c_status == "completed"
        assert f_status == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notifies_parent_via_task_queue(test_database):
    """Child crash pushes notification to parent's task_queue."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        _report_crash(ctx["url"], child)

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchone()

        assert row is not None, "parent should have crash notification in task_queue"
        payload = json.loads(row[0])
        text = payload["message"]["parts"][0]["text"]
        assert "crashed" in text
        assert child in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notification_includes_error_and_stderr(test_database):
    """Crash notification message includes error and stderr text."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        _report_crash(
            ctx["url"],
            child,
            error="OOM killed",
            stderr="memory limit exceeded",
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        text = payload["message"]["parts"][0]["text"]
        assert "OOM killed" in text
        assert "memory limit exceeded" in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_records_platform_event_on_parent(test_database):
    """Child crash records child_crashed platform event on parent session."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        _report_crash(ctx["url"], child)

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform'",
                (lead_sid,),
            ).fetchall()

        crashed_events = []
        for (payload_str,) in rows:
            payload = json.loads(payload_str)
            for part in payload.get("parts", []):
                if part.get("kind") == "data":
                    data = part["data"]
                    if data.get("type") == "child_crashed":
                        crashed_events.append(data)

        assert len(crashed_events) == 1
        ev = crashed_events[0]
        assert ev["child_session_id"] == child
        assert ev["agent_name"] == "lead-agent"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_root_lead_fails_execution(test_database):
    """Root lead crash propagates to execution → failed."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        _report_crash(ctx["url"], lead_sid)

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()

        assert row[0] == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_child_does_not_fail_execution(test_database):
    """Child crash does NOT propagate to execution."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        _report_crash(ctx["url"], child)

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()

        assert row[0] == "working", (
            f"execution should stay working when child crashes, got {row[0]}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_deep_tree_cascade(test_database):
    """Mid-tree crash cascades to grandchild and notifies parent."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        grandchild = _create_child_session(
            ctx, child, exec_id, agent_id, status="working"
        )

        _report_crash(ctx["url"], child)

        with db_conn(ctx["db_url"]) as conn:
            child_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child,)
            ).fetchone()[0]
            gc_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (grandchild,)
            ).fetchone()[0]
            # Lead should have crash notification
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchone()

        assert child_status == "failed"
        assert gc_status == "canceled"
        assert row is not None, "lead should have crash notification"
        text = json.loads(row[0])["message"]["parts"][0]["text"]
        assert "crashed" in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_already_terminal_is_noop(test_database):
    """Crash report on already-terminal session is a no-op."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        # Manually set session to canceled
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'canceled' WHERE id = ?",
                (lead_sid,),
            )
            conn.commit()
            event_count_before = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchone()[0]

        _report_crash(ctx["url"], lead_sid)

        with db_conn(ctx["db_url"]) as conn:
            status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (lead_sid,)
            ).fetchone()[0]
            event_count_after = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchone()[0]

        assert status == "canceled"
        assert event_count_after == event_count_before


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancelled_does_not_cascade_or_notify(test_database):
    """Cancelled error_kind does NOT cascade children or notify parent."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        grandchild = _create_child_session(
            ctx, child, exec_id, agent_id, status="working"
        )

        # Report cancelled (not crash)
        _report_crash(ctx["url"], child, error_kind="cancelled")

        with db_conn(ctx["db_url"]) as conn:
            child_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child,)
            ).fetchone()[0]
            gc_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (grandchild,)
            ).fetchone()[0]
            # No notification to parent
            tq_row = conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchone()[0]

        assert child_status == "canceled"
        assert gc_status == "working", "grandchild should NOT be cascaded for cancel"
        assert tq_row == 0, "no parent notification for cancel"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_idempotent_double_report(test_database):
    """Double crash report is idempotent — only one state_change event."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        _report_crash(ctx["url"], lead_sid)
        _report_crash(ctx["url"], lead_sid)

        with db_conn(ctx["db_url"]) as conn:
            status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (lead_sid,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchall()

        assert status == "failed"
        failed_events = [r for r in rows if json.loads(r[0]).get("to") == "failed"]
        assert len(failed_events) == 1, (
            f"expected exactly 1 failed state_change event, got {len(failed_events)}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notification_omits_stderr_when_none(test_database):
    """Crash notification omits stderr section when stderr is None."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        child = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        # Report crash with no stderr
        result_payload = {
            "sessionId": child,
            "error": "process died",
            "errorKind": "executor_failed",
        }
        resp = httpx.post(
            f"{ctx['url']}/api/worker/sync",
            json={"sessionResult": result_payload},
            timeout=10,
        )
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (lead_sid,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        text = payload["message"]["parts"][0]["text"]
        assert "process died" in text
        assert "Stderr" not in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_error_without_error_kind_still_fails_session(test_database):
    """Error without error_kind still fails the session (fail closed)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        _worker_sync(ctx["url"])

        # Send error without errorKind
        _report_crash(ctx["url"], lead_sid, error="something broke", error_kind=None)

        with db_conn(ctx["db_url"]) as conn:
            status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (lead_sid,)
            ).fetchone()[0]

        assert status == "failed", (
            f"session should be failed even without error_kind, got {status}"
        )
