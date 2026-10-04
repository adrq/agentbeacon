# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
import uuid
from datetime import datetime

import pytest

from tests.testhelpers import (
    assert_event_exists,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    get_session_row,
    insert_task,
    make_executor_report,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_resumable_crash_is_retried(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="recovery-agent", agent_type=agent_type
        )
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-123",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], sid)
        assert row["executor_state"] == "unassigned"
        assert row["recovery_attempts"] == 1
        assert row["desired"] == "run"
        assert row["outcome"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_resumable_crash_without_cwd_fails(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], agent_type="claude_sdk")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-abc",
            cwd=None,
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], session_id)
        assert row["outcome"] == "failed"
        assert row["desired"] == "terminate"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_with_pending_turns_and_no_agent_session_is_retried(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id=None,
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")
        insert_task(ctx["db_url"], exec_id, sid, "initial prompt")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], sid)
        assert row["executor_state"] == "unassigned"
        assert row["recovery_attempts"] == 1
        assert row["outcome"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_with_nothing_to_resume_or_process_fails(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id=None,
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], sid, desired="terminate", outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_non_resumable_agent_crash_fails(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="non-resumable-agent", agent_type="acp"
        )
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-456",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], sid, desired="terminate", outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_after_recovery_attempts_are_exhausted_fails(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-789",
            cwd="/tmp/test-cwd",
            recovery_attempts=2,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], sid, desired="terminate", outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_budget_reset_on_idle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            recovery_attempts=1,
            worker_id="worker-1",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(
            ctx["url"],
            "worker-1",
            executor_report=make_executor_report(sid, "idle"),
        )

        row = get_session_row(ctx["db_url"], sid)
        assert row["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_budget_not_reset_on_running(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="unassigned",
            recovery_attempts=1,
            worker_id="worker-1",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(
            ctx["url"],
            "worker-1",
            executor_report=make_executor_report(sid, "running"),
        )

        row = get_session_row(ctx["db_url"], sid)
        assert row["recovery_attempts"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_deleted_canceled_not_failed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="doomed-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE agents SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (agent_id,),
            )
            conn.commit()

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("outcome") == "canceled":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        row = get_session_row(ctx["db_url"], sid)
        assert row["desired"] == "terminate"
        assert row["outcome"] == "canceled"
        assert row["desired_by"] == "system:agent_deleted"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_consecutive_crashes_exhaust_budget(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="recovery-agent", agent_type=agent_type
        )
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-loop",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if (
                row.get("executor_state") == "unassigned"
                and row.get("recovery_attempts") == 1
            ):
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)
        row = get_session_row(ctx["db_url"], sid)
        assert row["executor_state"] == "unassigned"
        assert row["recovery_attempts"] == 1

        set_session_fields(ctx["db_url"], sid, executor_state="crashed")
        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if (
                row.get("executor_state") == "unassigned"
                and row.get("recovery_attempts") == 2
            ):
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)
        row = get_session_row(ctx["db_url"], sid)
        assert row["executor_state"] == "unassigned"
        assert row["recovery_attempts"] == 2

        set_session_fields(ctx["db_url"], sid, executor_state="crashed")
        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)
        assert_session_state(ctx["db_url"], sid, desired="terminate", outcome="failed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_resume_with_payload(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="recovery-agent", agent_type=agent_type
        )
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-resume",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")
        insert_task(ctx["db_url"], exec_id, sid, "queued work")

        post_worker_sync(ctx["url"], worker_id="tick-1")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="tick-1")
            time.sleep(0.25)

        resp = post_worker_sync(ctx["url"], worker_id="w-recovery")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["session_id"] == sid
        assert resp["action"]["payload"] is not None
        assert resp["action"]["resume"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_resume_without_payload(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="recovery-agent", agent_type=agent_type
        )
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-resume-nopayload",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="tick-1")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="tick-1")
            time.sleep(0.25)

        resp = post_worker_sync(ctx["url"], worker_id="w-recovery")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["session_id"] == sid
        assert resp["action"]["payload"] is None
        assert resp["action"]["resume"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_retry_with_agent_session_emits_may_have_been_lost_event(
    test_database, agent_type
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="recovery-agent", agent_type=agent_type
        )
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id="sdk-session-event",
            cwd="/tmp/test-cwd",
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_event_exists(
            ctx["db_url"],
            sid,
            "platform",
            payload_fragment="may have been lost",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_retry_without_agent_session_emits_no_platform_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id=None,
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")
        insert_task(ctx["db_url"], exec_id, sid, "initial prompt")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        with db_conn(ctx["db_url"]) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'platform'",
                (sid,),
            )
            count = cur.fetchone()[0]
        assert count == 0, f"Expected no platform events, found {count}"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_permanent_failure_emits_failed_permanently_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
            agent_session_id=None,
            recovery_attempts=0,
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="w-trigger")
        for _ in range(20):
            row = get_session_row(ctx["db_url"], sid)
            if row.get("outcome") == "failed":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_event_exists(
            ctx["db_url"],
            sid,
            "platform",
            payload_fragment="failed permanently",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_detect_crash_clears_worker_id_and_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="worker-dead",
            command_token=str(uuid.uuid4()),
            command_type="feed_turn",
            command_has_payload=True,
            command_at="2020-01-01T00:00:00Z",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], worker_id="probe-worker")

        row = get_session_row(ctx["db_url"], sid)
        assert row["executor_state"] == "crashed"
        assert row["worker_id"] is None
        assert row["command_token"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_set_executor_state_crashed_preserves_worker_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="worker-alive",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(
            ctx["url"],
            "worker-alive",
            executor_report=make_executor_report(sid, "crashed"),
        )

        row = get_session_row(ctx["db_url"], sid)
        assert row["executor_state"] == "crashed"
        assert row["worker_id"] == "worker-alive"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_set_executor_state_crashed_clears_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="worker-alive",
            command_token=str(uuid.uuid4()),
            command_type="stop_turn",
            command_has_payload=False,
            command_at=datetime.utcnow().isoformat(),
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(
            ctx["url"],
            "worker-alive",
            executor_report=make_executor_report(sid, "crashed"),
        )

        row = get_session_row(ctx["db_url"], sid)
        assert row["command_token"] is None
        assert row["command_type"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_crash_with_payload_command_emits_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recovery-agent")
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="worker-alive",
            command_token=str(uuid.uuid4()),
            command_type="feed_turn",
            command_has_payload=True,
            command_at=datetime.utcnow().isoformat(),
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(
            ctx["url"],
            "worker-alive",
            executor_report=make_executor_report(sid, "crashed"),
        )

        assert_event_exists(
            ctx["db_url"],
            sid,
            "platform",
            payload_fragment="message may have been lost",
        )
