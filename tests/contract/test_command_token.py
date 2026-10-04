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
    assert_command_pending,
    assert_no_command,
    assert_session_state,
    get_session_row,
    insert_task,
    make_executor_report,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_assign_command_for_pending_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "do work")

        resp = post_worker_sync(ctx["url"], worker_id="w1")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["session_id"] == session_id
        assert resp["action"]["payload"] is not None
        assert resp["action"]["resume"] is False
        assert "token" in resp

        assert_command_pending(ctx["db_url"], session_id, "assign", has_payload=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_assign_resume_without_payload(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            agent_session_id="sdk-session-1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], worker_id="w2")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["session_id"] == session_id
        assert resp["action"]["resume"] is True
        assert resp["action"]["payload"] is None

        assert_command_pending(ctx["db_url"], session_id, "assign", has_payload=False)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_assign_resume_with_pending_turns(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            agent_session_id="sdk-session-2",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w3")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["session_id"] == session_id
        assert resp["action"]["resume"] is True
        assert resp["action"]["payload"] is not None

        assert_command_pending(ctx["db_url"], session_id, "assign", has_payload=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_feed_turn_to_idle_worker(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="idle",
            worker_id="w4",
        )
        insert_task(ctx["db_url"], exec_id, session_id, "next turn please")

        resp = post_worker_sync(ctx["url"], worker_id="w4")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"
        assert resp["action"]["session_id"] == session_id
        assert resp["action"]["payload"] is not None
        assert "token" in resp

        assert_command_pending(ctx["db_url"], session_id, "feed_turn", has_payload=True)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_feed_turn_mid_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            worker_id="w5",
        )
        insert_task(ctx["db_url"], exec_id, session_id, "mid-turn message")

        resp = post_worker_sync(ctx["url"], worker_id="w5")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "feed_turn"
        assert resp["action"]["session_id"] == session_id
        assert "token" in resp

        assert_command_pending(ctx["db_url"], session_id, "feed_turn")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_turn_to_running_worker(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="running",
            worker_id="w6",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w6")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        assert resp["action"]["session_id"] == session_id
        assert "token" in resp

        assert_command_pending(
            ctx["db_url"], session_id, "stop_turn", has_payload=False
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cancel_command_issued(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="running",
            outcome="canceled",
            worker_id="w7",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w7")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "cancel"
        assert resp["action"]["session_id"] == session_id
        assert "token" in resp

        assert_command_pending(ctx["db_url"], session_id, "cancel")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ack_clears_command_token(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w8")
        assert resp["type"] == "command"
        token = resp["token"]

        _resp2 = post_worker_sync(
            ctx["url"],
            worker_id="w8",
            executor_report=make_executor_report(session_id, "running", "sdk-1"),
            command_ack=token,
        )

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_payload_command_ack_on_acceptance(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w9")
        assert resp["type"] == "command"
        assign_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            worker_id="w9",
            executor_report=make_executor_report(session_id, "running", "sdk-1"),
            command_ack=assign_token,
        )

        assert_no_command(ctx["db_url"], session_id)
        assert_session_state(ctx["db_url"], session_id, executor_state="running")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_control_command_ack_on_completion(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="running",
            worker_id="w10",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w10")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        stop_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            worker_id="w10",
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=stop_token,
        )

        assert_no_command(ctx["db_url"], session_id)
        assert_session_state(ctx["db_url"], session_id, executor_state="idle")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_token_ack_silently_dropped(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w11")
        assert resp["type"] == "command"
        real_token = resp["token"]

        stale_token = "stale-token-" + str(uuid.uuid4())
        _resp2 = post_worker_sync(
            ctx["url"],
            worker_id="w11",
            command_ack=stale_token,
        )

        assert_command_pending(ctx["db_url"], session_id, "assign")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_token"] == real_token


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ack_retry_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w12")
        assert resp["type"] == "command"
        token = resp["token"]

        post_worker_sync(
            ctx["url"],
            worker_id="w12",
            executor_report=make_executor_report(session_id, "running", "sdk-1"),
            command_ack=token,
        )
        assert_no_command(ctx["db_url"], session_id)

        _resp2 = post_worker_sync(
            ctx["url"],
            worker_id="w12",
            executor_report=make_executor_report(session_id, "running", "sdk-1"),
            command_ack=token,
        )

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_at_most_one_pending_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w13")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        original_token = resp["token"]

        insert_task(ctx["db_url"], exec_id, session_id, "another message")

        resp2 = post_worker_sync(ctx["url"], worker_id="w13")
        if resp2["type"] == "command":
            assert resp2["token"] == original_token, "must not issue a new token"
            assert resp2["action"]["type"] == "assign"
        else:
            assert resp2["type"] == "no_action"

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_token"] == original_token
        assert row["command_type"] == "assign"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_priority_override_cancel_over_feed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            worker_id="w14",
            command_token="feed-token-1",
            command_type="feed_turn",
            command_has_payload=True,
            command_at=datetime.utcnow().isoformat(),
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w14")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "cancel"
        assert resp["token"] != "feed-token-1"

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_type"] == "cancel"
        assert row["command_token"] != "feed-token-1"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_priority_override_stop_turn_over_assign(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w15")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assign_token = resp["token"]

        set_session_fields(ctx["db_url"], session_id, desired="stop")

        resp = post_worker_sync(ctx["url"], worker_id="w15")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"
        assert resp["token"] != assign_token

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_type"] == "stop_turn"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_priority_override_cancel_over_stop_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="stop",
            executor_state="running",
            worker_id="w16",
            command_token="stop-token-1",
            command_type="stop_turn",
            command_has_payload=False,
            command_at=datetime.utcnow().isoformat(),
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w16")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "cancel"
        assert resp["token"] != "stop-token-1"

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_type"] == "cancel"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_non_preemptive_feed_turn_cannot_override_assign(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w17")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assign_token = resp["token"]

        insert_task(ctx["db_url"], exec_id, session_id, "follow-up message")

        _resp2 = post_worker_sync(ctx["url"], worker_id="w17")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_token"] == assign_token
        assert row["command_type"] == "assign"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_command_timeout_triggers_detect_crash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w18")
        assert resp["type"] == "command"

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(ctx["url"], worker_id="w18-probe")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["worker_id"] is None, "DetectCrash must clear worker_id"
        assert row["command_token"] is None, "DetectCrash must clear command"
        assert row["command_type"] is None
        if row["outcome"] == "failed":
            assert row["desired"] == "terminate"
        else:
            assert row["executor_state"] == "crashed", (
                f"Post-assign-timeout: must be crashed or terminal failed, got {row['executor_state']}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_heartbeat_timeout_triggers_detect_crash(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "1"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w19")
        assert resp["type"] == "command"
        token = resp["token"]

        post_worker_sync(
            ctx["url"],
            worker_id="w19",
            executor_report=make_executor_report(session_id, "running", "sdk-1"),
            command_ack=token,
        )

        time.sleep(2)

        post_worker_sync(ctx["url"], worker_id="w19-probe")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["worker_id"] is None, "DetectCrash must clear worker_id"
        assert row["executor_state"] in ("crashed", "unassigned"), (
            f"Post-timeout state must be crashed or unassigned, got {row['executor_state']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_payload_command_timeout_emits_platform_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w20")
        assert resp["type"] == "command"

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(ctx["url"], worker_id="w20-probe")

        assert_event_exists(
            ctx["db_url"],
            session_id,
            "platform",
            "message may have been lost",
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_resume_without_payload_timeout_no_event(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="unassigned",
            agent_session_id="sdk-session-resume",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], worker_id="w21")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"
        assert resp["action"]["payload"] is None

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(ctx["url"], worker_id="w21-probe")

        with db_conn(ctx["db_url"]) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = ? AND payload LIKE ?",
                (session_id, "platform", "%message may have been lost%"),
            )
            count = cur.fetchone()[0]
        assert count == 0, (
            "resume-without-payload timeout must NOT emit message-loss event"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_override_discards_payload(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            worker_id="w22",
            command_token="feed-token-override",
            command_type="feed_turn",
            command_has_payload=True,
            command_at=datetime.utcnow().isoformat(),
        )

        set_session_fields(ctx["db_url"], session_id, desired="stop")

        resp = post_worker_sync(ctx["url"], worker_id="w22")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "stop_turn"

        row = get_session_row(ctx["db_url"], session_id)
        assert row["command_type"] == "stop_turn"
        assert row["command_token"] != "feed-token-override"

        with db_conn(ctx["db_url"]) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = ? AND payload LIKE ?",
                (session_id, "platform", "%message may have been lost%"),
            )
            count = cur.fetchone()[0]
        assert count == 0, "override-induced loss must NOT emit a platform event"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminal_cancel_ack_clears_worker_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="running",
            outcome="canceled",
            worker_id="w23",
        )

        resp = post_worker_sync(ctx["url"], worker_id="w23")
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "cancel"
        cancel_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            worker_id="w23",
            executor_report=make_executor_report(session_id, "idle"),
            command_ack=cancel_token,
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["worker_id"] is None
        assert row["command_token"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_command_timeout_live_worker_supervisor_kill(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w24")
        assert resp["type"] == "command"

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(ctx["url"], worker_id="w24")

        post_worker_sync(ctx["url"], worker_id="w24-probe")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["worker_id"] is None, "DetectCrash must clear worker_id"
        assert row["command_token"] is None, "DetectCrash must clear command"
        if row["outcome"] == "failed":
            assert row["desired"] == "terminate"
        else:
            assert row["executor_state"] == "crashed", (
                f"Post-assign-timeout: must be crashed or terminal failed, got {row['executor_state']}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_running_no_pending_turns_no_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="run",
            executor_state="running",
            worker_id="w25",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], worker_id="w25")

        assert resp["type"] == "no_action"
        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_idle_worker_sends_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
            worker_id="w26",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        resp = post_worker_sync(ctx["url"], worker_id="w26")

        assert resp["type"] == "command"
        assert resp["action"]["type"] == "cancel"
        assert resp["action"]["session_id"] == session_id

        assert_command_pending(ctx["db_url"], session_id, "cancel")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_idle_no_worker_finalizes_directly(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
            worker_id=None,
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        _resp = post_worker_sync(ctx["url"], worker_id="w27-unrelated")

        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_kill_confirmation_gates_reassignment(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "1"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "start")

        resp = post_worker_sync(ctx["url"], worker_id="w28")
        assert resp["type"] == "command"
        assign_token = resp["token"]

        post_worker_sync(
            ctx["url"],
            worker_id="w28",
            executor_report=make_executor_report(session_id, "running", "sdk-1"),
            command_ack=assign_token,
        )

        time.sleep(2)

        post_worker_sync(ctx["url"], worker_id="w28-probe")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["worker_id"] is None, "DetectCrash must clear worker_id"
        assert row["executor_state"] in ("crashed", "unassigned"), (
            f"Post-timeout state must be crashed or unassigned, got {row['executor_state']}"
        )

        resp2 = post_worker_sync(ctx["url"], worker_id="w28-new")

        if resp2["type"] == "command" and resp2["action"]["type"] == "assign":
            assert resp2["action"]["session_id"] == session_id
            assert resp2["action"]["resume"] is True
        else:
            assert resp2["type"] == "no_action", resp2
