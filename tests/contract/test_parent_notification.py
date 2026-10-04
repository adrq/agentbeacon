# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

import httpx
import pytest

from tests.testhelpers import (
    assert_event_exists,
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    create_child_session_raw,
    get_execution_row,
    get_session_row,
    get_task_queue_count,
    get_task_queue_payload,
    insert_task,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


WORKER_ID = "test-worker-pn"


def _setup_parent_child(
    ctx,
    *,
    parent_desired="run",
    parent_executor_state="idle",
    child_desired="run",
    child_executor_state="crashed",
    child_outcome=None,
):
    lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent-pn")
    child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent-pn")
    exec_id, parent_id = create_execution_via_api(
        ctx["url"], lead_agent_id, "coordinate"
    )

    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
            (exec_id, child_agent_id),
        )
        conn.commit()

    child_worker = (
        "w-child"
        if child_executor_state in ("running", "idle") and child_outcome is None
        else None
    )

    child_id = create_child_session_raw(
        ctx["db_url"],
        parent_id,
        exec_id,
        child_agent_id,
        desired=child_desired,
        executor_state=child_executor_state,
        outcome=child_outcome,
        worker_id=child_worker,
    )

    parent_worker = "w-parent" if parent_executor_state in ("running", "idle") else None

    set_session_fields(
        ctx["db_url"],
        parent_id,
        desired=parent_desired,
        executor_state=parent_executor_state,
        agent_session_id="parent-sdk-1",
        worker_id=parent_worker,
    )

    with db_conn(ctx["db_url"]) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (parent_id,))
        conn.commit()

    return exec_id, parent_id, child_id, child_agent_id


def _wait_for_parent_task(ctx, parent_id, max_wait=5.0):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if get_task_queue_count(ctx["db_url"], parent_id) > 0:
            return True
        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
        time.sleep(0.25)
    return False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unrecoverable_child_crash_notifies_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        assert _wait_for_parent_task(ctx, parent_id), (
            "parent task_queue should have a notification after child crash"
        )
        assert get_task_queue_count(ctx["db_url"], parent_id) == 1

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_crashed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_notification_coexists_with_existing_queue_work(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="crashed",
        )

        insert_task(ctx["db_url"], exec_id, parent_id, "existing work", source="user")
        before_count = get_task_queue_count(ctx["db_url"], parent_id)
        assert before_count >= 1

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )
        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        assert _wait_for_parent_task(ctx, parent_id), (
            "notification should be enqueued even with existing work"
        )

        after_count = get_task_queue_count(ctx["db_url"], parent_id)
        assert after_count == before_count + 1, (
            f"notification must coexist with existing work: before={before_count}, after={after_count}"
        )

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_crashed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_parent_notified_set_true_after_delivery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        _wait_for_parent_task(ctx, parent_id)

        child_row = get_session_row(ctx["db_url"], child_id)
        assert bool(child_row["parent_notified"]) is True

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_crashed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_duplicate_notification_suppressed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
            parent_notified=True,
        )

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        post_worker_sync(ctx["url"], worker_id="w-trigger")

        count = get_task_queue_count(ctx["db_url"], parent_id)
        assert count == 0, f"expected 0 tasks (dedup), got {count}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_dead_parent_skips_notification(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            parent_desired="terminate",
            parent_executor_state="idle",
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(ctx["db_url"], parent_id, outcome="completed")

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        post_worker_sync(ctx["url"], worker_id="w-trigger")

        count = get_task_queue_count(ctx["db_url"], parent_id)
        assert count == 0, "no notification should be sent to a dead parent"

        child_row = get_session_row(ctx["db_url"], child_id)
        assert bool(child_row["parent_notified"]) is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stopped_parent_auto_resumes_on_crash_notification(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            parent_desired="stop",
            parent_executor_state="idle",
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        _wait_for_parent_task(ctx, parent_id)

        assert_session_state(ctx["db_url"], parent_id, desired="run")

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_crashed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_stop_on_child_notifies_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="running",
            child_outcome=None,
        )

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{child_id}/stop", timeout=5)
        assert resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], parent_id) >= 1, (
            "parent should be notified atomically when user stops a child"
        )

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_stopped")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_terminate_on_child_notifies_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="idle",
            child_outcome=None,
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (child_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], parent_id) >= 1, (
            "parent should be notified atomically when user terminates a child"
        )

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_terminated")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_release_does_not_notify_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-release")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-release")
        exec_id, parent_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            parent_id,
            "delegate",
            {"agent": "child-release", "prompt": "do work"},
        )
        payload = json.loads(result["content"][0]["text"])
        child_id = payload["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (parent_id,))
            conn.commit()

        with db_conn(ctx["db_url"]) as conn:
            events_before = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]

        result = mcp_tools_call(
            ctx["url"],
            parent_id,
            "release",
            {"session_id": child_id},
        )
        assert result.get("isError") is not True, "release should succeed"

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        post_worker_sync(ctx["url"], worker_id="w-trigger")

        assert_session_state(ctx["db_url"], child_id, desired="terminate")

        count = get_task_queue_count(ctx["db_url"], parent_id)
        assert count == 0, "agent release should not notify parent"

        with db_conn(ctx["db_url"]) as conn:
            events_after = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]
        assert events_after == events_before, (
            "agent release should not write platform event on parent"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cascade_does_not_notify_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="unassigned",
            child_outcome=None,
        )

        with db_conn(ctx["db_url"]) as conn:
            events_before = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]

        set_session_fields(
            ctx["db_url"],
            parent_id,
            desired="terminate",
            outcome="completed",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        for _ in range(20):
            child_row = get_session_row(ctx["db_url"], child_id)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        child_row = get_session_row(ctx["db_url"], child_id)
        assert child_row["desired"] == "terminate"
        assert child_row["desired_by"] == "system:cascade"

        count = get_task_queue_count(ctx["db_url"], parent_id)
        assert count == 0, "cascade should not notify parent"

        with db_conn(ctx["db_url"]) as conn:
            events_after = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]
        assert events_after == events_before, (
            "cascade should not write platform event on parent"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_repeated_stop_noop_does_not_notify(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="stop",
            child_executor_state="idle",
            child_outcome=None,
        )

        with db_conn(ctx["db_url"]) as conn:
            events_before = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{child_id}/stop", timeout=5)
        assert resp.status_code == 200

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        post_worker_sync(ctx["url"], worker_id="w-trigger")

        count = get_task_queue_count(ctx["db_url"], parent_id)
        assert count == 0, "repeated same-value stop should not notify parent"

        with db_conn(ctx["db_url"]) as conn:
            events_after = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]
        assert events_after == events_before, (
            "repeated stop should not write platform event on parent"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_action_notification_auto_resumes_stopped_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            parent_desired="stop",
            parent_executor_state="idle",
            child_desired="run",
            child_executor_state="running",
            child_outcome=None,
        )

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{child_id}/stop", timeout=5)
        assert resp.status_code == 200

        assert get_task_queue_count(ctx["db_url"], parent_id) >= 1, (
            "parent should be notified atomically when user stops a child"
        )

        assert_session_state(ctx["db_url"], parent_id, desired="run")

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_stopped")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_notification_rejected_by_execution_write_barrier(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="crashed",
        )

        with db_conn(ctx["db_url"]) as conn:
            events_before = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]

        set_execution_fields(ctx["db_url"], exec_id, desired="terminate")
        set_session_fields(
            ctx["db_url"],
            parent_id,
            desired="run",
            executor_state="idle",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        post_worker_sync(ctx["url"], worker_id="w-trigger")

        count = get_task_queue_count(ctx["db_url"], parent_id)
        assert count == 0, "execution write barrier should prevent notification"

        with db_conn(ctx["db_url"]) as conn:
            events_after = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (parent_id,),
            ).fetchone()[0]
        assert events_after == events_before, (
            "execution write barrier should prevent platform event on parent"
        )

        child_row = get_session_row(ctx["db_url"], child_id)
        assert bool(child_row["parent_notified"]) is False, (
            "parent_notified must stay FALSE when write barrier rejects notification"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_notification_content_is_structured_platform_message(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        assert _wait_for_parent_task(ctx, parent_id)

        payload = get_task_queue_payload(ctx["db_url"], parent_id)
        assert payload is not None
        assert "message" in payload
        msg = payload["message"]
        assert "parts" in msg
        assert len(msg["parts"]) > 0
        data_parts = [p for p in msg["parts"] if "data" in p]
        assert len(data_parts) >= 1, "crash notification should have a data part"
        data = data_parts[0]["data"]
        assert data["type"] == "child_crashed"
        assert data["child_session_id"] == child_id

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_crashed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unnotified_failed_child_blocks_completion(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            parent_desired="run",
            parent_executor_state="idle",
            child_desired="terminate",
            child_executor_state="crashed",
            child_outcome="failed",
        )

        set_session_fields(ctx["db_url"], child_id, parent_notified=False)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (parent_id,))
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (child_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        exec_row = get_execution_row(ctx["db_url"], exec_id)
        assert exec_row["outcome"] == "canceled", (
            "execution outcome is not canceled while the failed child's parent is unnotified"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_failed_child_does_not_block_completion_when_parent_has_an_outcome(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            parent_desired="terminate",
            parent_executor_state="idle",
            child_desired="terminate",
            child_executor_state="crashed",
            child_outcome="failed",
        )

        set_session_fields(
            ctx["db_url"],
            parent_id,
            outcome="completed",
        )

        set_session_fields(ctx["db_url"], child_id, parent_notified=False)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (parent_id,))
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (child_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        exec_row = get_execution_row(ctx["db_url"], exec_id)
        assert exec_row["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_failed_child_stops_blocking_completion_once_parent_is_notified(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, parent_id, child_id, _ = _setup_parent_child(
            ctx,
            parent_desired="run",
            parent_executor_state="idle",
            child_desired="run",
            child_executor_state="crashed",
        )

        set_session_fields(
            ctx["db_url"],
            child_id,
            desired="terminate",
            outcome="failed",
            desired_by="system:crash_unrecoverable",
        )

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
        _wait_for_parent_task(ctx, parent_id)

        child_row = get_session_row(ctx["db_url"], child_id)
        assert bool(child_row["parent_notified"]) is True

        assert_event_exists(ctx["db_url"], parent_id, "platform", "child_crashed")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (parent_id,))
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (child_id,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            parent_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        post_worker_sync(ctx["url"], worker_id="reconcile-trigger")

        exec_row = get_execution_row(ctx["db_url"], exec_id)
        assert exec_row["outcome"] == "completed", (
            "after real notification delivery and parent quiescent, execution should complete"
        )
