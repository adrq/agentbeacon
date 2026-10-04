# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import time

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_execution_state,
    assert_session_state,
    get_execution_row,
    get_session_row,
    insert_task,
    make_executor_report,
    make_turn_result,
    set_config,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_result_keeps_session_running(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")
        worker_id = "w-test-1"

        data = post_worker_sync(ctx["url"], worker_id)
        assert data["type"] == "command"

        token = data["token"]
        post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
            turn_result=make_turn_result(
                session_id,
                messages=[{"role": "ROLE_AGENT", "parts": [{"text": "first turn"}]}],
            ),
            command_ack=token,
        )

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
        )
        assert_execution_state(ctx["db_url"], exec_id, desired="run")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_result_delivers_to_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, agent_id),
            )
            conn.commit()
        lead_worker_id = "w-lead"
        child_worker_id = "w-child"

        data = post_worker_sync(ctx["url"], lead_worker_id)
        assert data["type"] == "command"
        lead_token = data["token"]

        post_worker_sync(
            ctx["url"],
            lead_worker_id,
            executor_report=make_executor_report(lead_id, "running"),
            command_ack=lead_token,
        )

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "test-agent", "prompt": "child task"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        child_data = post_worker_sync(ctx["url"], child_worker_id)
        child_token = child_data["token"]

        post_worker_sync(
            ctx["url"],
            child_worker_id,
            executor_report=make_executor_report(child_id, "running"),
            command_ack=child_token,
        )

        post_worker_sync(
            ctx["url"],
            child_worker_id,
            executor_report=make_executor_report(child_id, "idle"),
            turn_result=make_turn_result(
                child_id,
                messages=[{"role": "ROLE_AGENT", "parts": [{"text": "child output"}]}],
            ),
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id",
                (lead_id,),
            ).fetchall()
            assert len(rows) == 1, (
                "Parent should have exactly 1 queued notification from child turn-complete"
            )
            payload = json.loads(rows[0][0])
            parts = payload["message"]["parts"]
            data_parts = [p for p in parts if "data" in p]
            assert len(data_parts) >= 1, "notification should have a data part"
            data = data_parts[0]["data"]
            assert data["type"] == "turn_complete"
            assert data["child_session_id"] == child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_queued_task_triggers_feed_turn_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")
        worker_id = "w-test-3"

        data = post_worker_sync(ctx["url"], worker_id)
        assert data["type"] == "command"
        assign_token = data["token"]

        post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
            command_ack=assign_token,
        )

        post_worker_sync(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(
                session_id,
                messages=[{"role": "ROLE_AGENT", "parts": [{"text": "done"}]}],
            ),
        )

        insert_task(ctx["db_url"], exec_id, session_id, "queued msg")

        data = post_worker_sync(ctx["url"], worker_id)
        assert data["type"] == "command"
        assert data["action"]["type"] == "feed_turn"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_catches_crashed_root(test_database):
    SHORT_TIMEOUT = {
        "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "2",
        "AGENTBEACON_COMMAND_TIMEOUT_SECS": "2",
    }
    with scheduler_context(db_url=test_database, env=SHORT_TIMEOUT) as ctx1:
        agent_id = seed_test_agent(
            ctx1["db_url"], name="test-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(ctx1["url"], agent_id, "init")
        worker_id = "w-test-4"

        data = post_worker_sync(ctx1["url"], worker_id)
        assert data["type"] == "command"

        set_session_fields(
            ctx1["db_url"],
            session_id,
            executor_state="crashed",
            agent_session_id="sdk-abc",
            cwd="/tmp/test",
        )
        set_config(ctx1["db_url"], "restart.pause_sessions", "false")

    with scheduler_context(db_url=test_database, env=SHORT_TIMEOUT) as ctx2:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = get_session_row(ctx2["db_url"], session_id)
            if row.get("executor_state") != "crashed":
                break
            post_worker_sync(ctx2["url"], worker_id="reconcile-trigger")
            time.sleep(0.5)

        row = get_session_row(ctx2["db_url"], session_id)
        assert row["executor_state"] != "crashed", (
            f"Session should not remain crashed (got: {row['executor_state']})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_all_terminal_transitions_execution(test_database):
    SHORT_TIMEOUT = {
        "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "2",
        "AGENTBEACON_COMMAND_TIMEOUT_SECS": "2",
    }
    with scheduler_context(db_url=test_database, env=SHORT_TIMEOUT) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", [session_id])
            conn.commit()
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], "reconcile-trigger")

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = get_execution_row(ctx["db_url"], exec_id)
            if row.get("outcome") is not None:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.5)

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "completed", (
            f"Execution with all completed sessions should reconcile to completed (got: {row.get('outcome')})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_reconciliation_failed_session_transitions_execution_to_failed(test_database):
    SHORT_TIMEOUT = {
        "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "2",
        "AGENTBEACON_COMMAND_TIMEOUT_SECS": "2",
    }
    with scheduler_context(db_url=test_database, env=SHORT_TIMEOUT) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="run")

        post_worker_sync(ctx["url"], "reconcile-trigger")

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = get_execution_row(ctx["db_url"], exec_id)
            if row.get("outcome") is not None:
                break
            post_worker_sync(ctx["url"], "reconcile-trigger")
            time.sleep(0.5)

        row = get_execution_row(ctx["db_url"], exec_id)
        assert row["outcome"] == "failed", (
            f"Execution with failed session should reconcile to failed (got: {row.get('outcome')})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_attempts_incremented_on_crash(test_database):
    SHORT_TIMEOUT = {
        "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "2",
        "AGENTBEACON_COMMAND_TIMEOUT_SECS": "2",
    }
    with scheduler_context(db_url=test_database, env=SHORT_TIMEOUT) as ctx1:
        agent_id = seed_test_agent(
            ctx1["db_url"], name="test-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(ctx1["url"], agent_id, "init")

        set_session_fields(
            ctx1["db_url"],
            session_id,
            executor_state="crashed",
            agent_session_id="sdk-abc",
            cwd="/tmp/test",
        )
        set_config(ctx1["db_url"], "restart.pause_sessions", "false")

    with scheduler_context(db_url=test_database, env=SHORT_TIMEOUT) as ctx2:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = get_session_row(ctx2["db_url"], session_id)
            if (
                row.get("executor_state") != "crashed"
                or row.get("recovery_attempts", 0) > 0
            ):
                break
            post_worker_sync(ctx2["url"], worker_id="reconcile-trigger")
            time.sleep(0.3)

        row = get_session_row(ctx2["db_url"], session_id)
        assert (
            row.get("executor_state") != "crashed"
            or row.get("recovery_attempts", 0) > 0
        ), (
            f"Session should either be recovered or have incremented attempts "
            f"(got executor_state={row.get('executor_state')}, attempts={row.get('recovery_attempts')})"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_attempts_not_incremented_for_terminal_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "init")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        row_before = get_session_row(ctx["db_url"], session_id)
        before = row_before["recovery_attempts"]

        post_worker_sync(ctx["url"], worker_id="probe-worker")
        post_worker_sync(ctx["url"], worker_id="probe-worker")

        row_after = get_session_row(ctx["db_url"], session_id)
        after = row_after["recovery_attempts"]

        assert after == before, (
            f"recovery_attempts should not increment for terminal sessions "
            f"(before={before}, after={after})"
        )
