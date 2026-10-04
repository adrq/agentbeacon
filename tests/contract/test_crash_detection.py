# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_execution_state,
    assert_session_state,
    create_child_session_raw,
    get_session_row,
    get_task_queue_count,
    get_task_queue_payload,
    make_executor_report,
    make_turn_result,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_marks_session_crashed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        row = get_session_row(ctx["db_url"], lead_sid)
        assert row["executor_state"] == "crashed" or row["outcome"] == "failed", (
            f"Expected crashed or terminal failed, got executor_state={row['executor_state']}, outcome={row['outcome']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_state_change_event_includes_error_context(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
            turn_result=make_turn_result(
                lead_sid,
                error="broken pipe",
                error_kind="executor_failed",
            ),
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchall()

        crashed_events = []
        for (payload_str,) in rows:
            payload = json.loads(payload_str)
            if payload.get("executor_state") == "crashed":
                crashed_events.append(payload)

        assert len(crashed_events) == 1
        ev = crashed_events[0]
        assert ev["error"] == "broken pipe"
        assert ev["error_kind"] == "executor_failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_cascades_children(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child1 = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child1, worker_id="w-child1")
        child2 = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        for _ in range(20):
            r1 = get_session_row(ctx["db_url"], child1)
            r2 = get_session_row(ctx["db_url"], child2)
            if r1.get("desired") == "terminate" and r2.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], "cascade-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child1, desired="terminate", outcome="canceled"
        )
        assert_session_state(
            ctx["db_url"], child2, desired="terminate", outcome="completed"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_cascade_skips_terminal_children(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child_running = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child_running, worker_id="w-child-running")
        child_completed = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        child_failed = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        for _ in range(20):
            r = get_session_row(ctx["db_url"], child_running)
            if r.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], "cascade-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], child_running, desired="terminate", outcome="canceled"
        )
        assert_session_state(ctx["db_url"], child_completed, outcome="completed")
        assert_session_state(ctx["db_url"], child_failed, outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notifies_parent_via_task_queue(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w2")

        set_session_fields(ctx["db_url"], child, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child, "crashed"),
        )

        for _ in range(20):
            if get_task_queue_count(ctx["db_url"], lead_sid) > 0:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.25)

        payload = get_task_queue_payload(ctx["db_url"], lead_sid)
        assert payload is not None, (
            "parent should have crash notification in task_queue"
        )
        parts = payload["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1, "crash notification should have a data part"
        data = data_parts[0]["data"]
        assert data["type"] == "child_crashed"
        assert data["child_session_id"] == child

        row = get_session_row(ctx["db_url"], child)
        assert bool(row["parent_notified"]) is True, (
            "parent_notified should be TRUE after notification"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notification_includes_error_and_stderr(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w2")

        set_session_fields(ctx["db_url"], child, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child, "crashed"),
            turn_result=make_turn_result(
                child,
                error="OOM killed",
                error_kind="executor_failed",
            ),
        )

        for _ in range(20):
            if get_task_queue_count(ctx["db_url"], lead_sid) > 0:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.25)

        payload = get_task_queue_payload(ctx["db_url"], lead_sid)
        assert payload is not None
        parts = payload["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1, "crash notification should have a data part"
        data = data_parts[0]["data"]
        assert data["child_session_id"] == child


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notifies_parent_via_task_queue_parked_lead(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        set_session_fields(
            ctx["db_url"],
            lead_sid,
            desired="run",
            executor_state="idle",
            command_token=None,
            command_type=None,
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (lead_sid,))
            conn.commit()

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w1")

        set_session_fields(ctx["db_url"], child, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(child, "crashed"),
        )

        for _ in range(20):
            if get_task_queue_count(ctx["db_url"], lead_sid) > 0:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.25)

        payload = get_task_queue_payload(ctx["db_url"], lead_sid)
        assert payload is not None, "parent should have crash notification"
        parts = payload["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1, "crash notification should have a data part"
        assert data_parts[0]["data"]["child_session_id"] == child


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_root_lead_fails_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        for _ in range(20):
            row = get_session_row(ctx["db_url"], lead_sid)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], lead_sid, outcome="failed")
        assert_execution_state(ctx["db_url"], exec_id, outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_child_does_not_fail_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w2")

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child, "crashed"),
        )

        row = get_session_row(ctx["db_url"], child)
        assert row["executor_state"] == "crashed" or row["outcome"] == "failed"
        assert_execution_state(ctx["db_url"], exec_id, outcome=None)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_deep_tree_cascade(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w2")
        grandchild = create_child_session_raw(
            ctx["db_url"],
            child,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], grandchild, worker_id="w-grandchild")

        set_session_fields(ctx["db_url"], child, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child, "crashed"),
        )

        for _ in range(20):
            gc_row = get_session_row(ctx["db_url"], grandchild)
            if gc_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], "cascade-trigger")
            time.sleep(0.25)

        assert_session_state(
            ctx["db_url"], grandchild, desired="terminate", outcome="canceled"
        )
        payload = None
        for _ in range(20):
            payload = get_task_queue_payload(ctx["db_url"], lead_sid)
            if payload is not None:
                break
            post_worker_sync(ctx["url"], "notify-trigger")
            time.sleep(0.25)
        assert payload is not None, "lead should have crash notification"
        parts = payload["message"]["parts"]
        data_parts = [p for p in parts if "data" in p]
        assert len(data_parts) >= 1
        assert data_parts[0]["data"]["child_session_id"] == child


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_already_terminal_is_noop(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'canceled', "
                "command_token = NULL, command_type = NULL, command_at = NULL, "
                "command_has_payload = FALSE WHERE id = ?",
                (lead_sid,),
            )
            conn.commit()
            event_count_before = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchone()[0]

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        with db_conn(ctx["db_url"]) as conn:
            event_count_after = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchone()[0]

        row = get_session_row(ctx["db_url"], lead_sid)
        assert row["outcome"] == "canceled"
        assert row["executor_state"] != "crashed", (
            "write barrier should prevent SetExecutorState(Crashed) on terminal session"
        )
        assert event_count_after == event_count_before


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancelled_does_not_cascade_or_notify(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w2")
        grandchild = create_child_session_raw(
            ctx["db_url"],
            child,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
            worker_id="w-grandchild",
        )

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child, "idle"),
            turn_result=make_turn_result(child, error_kind="cancelled"),
        )

        assert_session_state(ctx["db_url"], child, executor_state="idle")
        assert_session_state(ctx["db_url"], grandchild, desired="run", outcome=None)
        assert get_task_queue_count(ctx["db_url"], lead_sid) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_idempotent_double_report(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        set_session_fields(ctx["db_url"], lead_sid, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )
        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        row = get_session_row(ctx["db_url"], lead_sid)
        if row["outcome"] == "failed":
            assert row["desired"] == "terminate"
        else:
            assert row["executor_state"] == "crashed"

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (lead_sid,),
            ).fetchall()

        crashed_events = [
            r for r in rows if json.loads(r[0]).get("executor_state") == "crashed"
        ]
        assert len(crashed_events) >= 1, (
            f"expected at least 1 crashed state_change event, got {len(crashed_events)}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notification_omits_stderr_when_none(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        child = create_child_session_raw(
            ctx["db_url"],
            lead_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="running",
        )
        set_session_fields(ctx["db_url"], child, worker_id="w2")

        set_session_fields(ctx["db_url"], child, recovery_attempts=2)

        post_worker_sync(
            ctx["url"],
            "w2",
            executor_report=make_executor_report(child, "crashed"),
        )

        for _ in range(20):
            if get_task_queue_count(ctx["db_url"], lead_sid) > 0:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.25)

        payload = get_task_queue_payload(ctx["db_url"], lead_sid)
        assert payload is not None
        text = payload["message"]["parts"][0]["text"]
        assert "crashed" in text
        assert "Stderr" not in text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_without_error_kind_still_crashes_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        post_worker_sync(ctx["url"], "w1")

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report=make_executor_report(lead_sid, "crashed"),
        )

        row = get_session_row(ctx["db_url"], lead_sid)
        assert row["executor_state"] == "crashed" or row["outcome"] == "failed", (
            f"Expected crashed or terminal failed, got executor_state={row['executor_state']}, outcome={row['outcome']}"
        )
