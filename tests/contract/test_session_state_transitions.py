# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

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
from tests.mock_agent_helpers import (
    assert_no_command,
    assert_session_state,
    get_session_row,
    get_task_queue_count,
    insert_task,
    make_executor_report,
    make_turn_result,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


WORKER_ID = "test-worker-1"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fresh_session_defaults(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            outcome=None,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_reports_running(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "command"
        token = resp["token"]

        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="agent-sess-1"
            ),
            command_ack=token,
        )

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            outcome=None,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_reports_idle_after_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        token = resp["token"]

        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="agent-sess-1"
            ),
            command_ack=token,
        )

        post_worker_sync(
            ctx["url"],
            WORKER_ID,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(session_id, messages=[]),
        )

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            outcome=None,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_user_stop_sets_desired_stop(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="stop")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_to_run_via_new_message(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert_session_state(ctx["db_url"], session_id, desired="stop")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "continue please"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="run")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_quiescent_idle_produces_completed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id="agent-sess-1",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="completed",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_while_running_produces_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            agent_session_id="agent-sess-1",
            worker_id="w-owner",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_idle_with_pending_turns_produces_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id="agent-sess-1",
            worker_id="w-idle",
        )

        insert_task(ctx["db_url"], exec_id, session_id, "extra work")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_pristine_unassigned_produces_completed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            agent_session_id=None,
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="completed",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_non_pristine_unassigned_produces_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            agent_session_id="orphaned-sdk-session",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_stopped_crashed_produces_completed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="crashed",
            agent_session_id="agent-sess-1",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="completed",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_crashed_with_desired_run_produces_canceled(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="crashed",
            agent_session_id="agent-sess-1",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ratchet_terminate_to_run_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "try to resume"}]},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ratchet_terminate_to_stop_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
            executor_state="running",
            worker_id="w-running",
        )

        resp = httpx.post(f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=5)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_write_barrier_send_message_rejected_on_terminal_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="completed",
            executor_state="idle",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "too late"}]},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_write_barrier_delegate_rejected_when_execution_terminating(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        set_execution_fields(ctx["db_url"], exec_id, desired="terminate")

        with db_conn(ctx["db_url"]) as conn:
            session_count_before = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE execution_id = ?",
                (exec_id,),
            ).fetchone()[0]

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "should fail"},
        )

        assert result.get("isError") is True

        with db_conn(ctx["db_url"]) as conn:
            session_count_after = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE execution_id = ?",
                (exec_id,),
            ).fetchone()[0]
        assert session_count_after == session_count_before, (
            "write barrier must prevent child session creation"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_provenance_user_terminate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        row = get_session_row(ctx["db_url"], session_id)
        assert row["desired_by"] == "user"
        assert row["desired_at"] is not None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_provenance_cascade(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
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
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "do child work"},
        )
        payload = json.loads(result["content"][0]["text"])
        child_session_id = payload["session_id"]

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{lead_session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        for _ in range(20):
            child_row = get_session_row(ctx["db_url"], child_session_id)
            if child_row.get("desired") == "terminate":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        child_row = get_session_row(ctx["db_url"], child_session_id)
        assert child_row["desired"] == "terminate"
        assert child_row["desired_by"] == "system:cascade"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_send_to_stopped_auto_resumes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="idle",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "resume now"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="run")
        assert get_task_queue_count(ctx["db_url"], session_id) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_active_predicate_running_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            agent_session_id="agent-sess-1",
            worker_id="w-running",
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stopped_idle_session_gets_no_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="idle",
            agent_session_id="agent-sess-1",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "no_action"

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stopped_unassigned_session_gets_no_action_and_terminates_canceled(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="unassigned",
            agent_session_id="orphaned-sdk-session",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "no_action"

        assert_no_command(ctx["db_url"], session_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stopped_crashed_session_gets_no_action_and_terminates_completed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="crashed",
            agent_session_id="agent-sess-1",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "no_action"

        assert_no_command(ctx["db_url"], session_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert_session_state(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="completed",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unassigned_session_without_pending_turns_gets_no_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            agent_session_id=None,
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "no_action"

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_session_without_pending_turns_gets_no_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            agent_session_id="agent-sess-1",
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], WORKER_ID)
        assert resp["type"] == "no_action"

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_during_stop_then_user_message_triggers_recovery(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="crashed",
            agent_session_id="agent-sess-1",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "try again"}]},
            timeout=5,
        )
        assert resp.status_code == 200

        assert_session_state(ctx["db_url"], session_id, desired="run")

        for _ in range(20):
            row = get_session_row(ctx["db_url"], session_id)
            if row.get("executor_state") == "unassigned":
                break
            post_worker_sync(ctx["url"], worker_id="reconcile-trigger")
            time.sleep(0.25)

        assert_session_state(ctx["db_url"], session_id, executor_state="unassigned")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_write_barrier_send_message_rejected_during_crash_gap(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(ctx["db_url"], session_id, desired="terminate")
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "should be rejected"}]},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_write_barrier_send_message_rejected_when_terminating(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_execution_fields(ctx["db_url"], exec_id, desired="terminate")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "too late"}]},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_write_barrier_send_message_rejected_when_outcome_set(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_execution_fields(
            ctx["db_url"],
            exec_id,
            desired="terminate",
            outcome="completed",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "too late"}]},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_deletion_guard_rejects_with_non_terminal_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="guarded-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "active work"
        )

        assert_session_state(ctx["db_url"], session_id, outcome=None)

        resp = httpx.delete(f"{ctx['url']}/api/v1/agents/{agent_id}", timeout=5)
        assert resp.status_code == 409
