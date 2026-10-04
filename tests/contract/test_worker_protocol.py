# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime

import pytest

from tests.testhelpers import (
    assert_event_exists,
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    get_session_row,
    make_executor_report,
    make_turn_result,
    set_session_fields,
    post_worker_sync,
)


WORKER_A = "test-worker-a"
WORKER_B = "test-worker-b"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_atomic_sync_all_fields(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        assert resp["type"] == "command"
        token = resp["token"]

        resp = post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            turn_result=make_turn_result(
                session_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "done"}]},
                ],
            ),
            command_ack=token,
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "running"
        assert row["agent_session_id"] == "sdk-1"
        assert row["command_token"] is None
        assert_event_exists(ctx["db_url"], session_id, "message", "done")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_processing_order_report_before_ack(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
            executor_state="running",
            command_token="cancel-token-1",
            command_type="cancel",
            command_has_payload=False,
            command_at=datetime.utcnow().isoformat(),
        )

        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack="cancel-token-1",
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "idle"
        assert row["command_token"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_worker_report_dropped(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        assert resp["type"] == "command"
        token = resp["token"]

        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        assert_session_state(ctx["db_url"], session_id, worker_id=WORKER_A)

        resp = post_worker_sync(
            ctx["url"],
            WORKER_B,
            executor_report=make_executor_report(session_id, "idle"),
        )

        assert resp["type"] == "no_action"

        assert_session_state(
            ctx["db_url"],
            session_id,
            executor_state="running",
            worker_id=WORKER_A,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stale_worker_triggers_supervisor_kill(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        resp = post_worker_sync(
            ctx["url"],
            WORKER_B,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "no_action"

        assert_session_state(
            ctx["db_url"],
            session_id,
            executor_state="running",
            worker_id=WORKER_A,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_result_accepted_during_stop(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        set_session_fields(ctx["db_url"], session_id, desired="stop")

        resp = post_worker_sync(
            ctx["url"],
            WORKER_A,
            turn_result=make_turn_result(
                session_id,
                messages=[
                    {
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "partial output before stop"}],
                    },
                ],
            ),
        )

        assert resp["type"] in ("no_action", "command")
        assert_event_exists(
            ctx["db_url"], session_id, "message", "partial output before stop"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_turn_result_accepted_during_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
        )

        resp = post_worker_sync(
            ctx["url"],
            WORKER_A,
            turn_result=make_turn_result(
                session_id,
                messages=[
                    {"role": "ROLE_AGENT", "parts": [{"text": "output during cancel"}]},
                ],
            ),
        )

        assert resp["type"] in ("no_action", "command")
        assert_event_exists(
            ctx["db_url"], session_id, "message", "output during cancel"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_id_recorded_on_assign(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        assert resp["type"] == "command"
        assert resp["action"]["type"] == "assign"

        row = get_session_row(ctx["db_url"], session_id)
        assert row["worker_id"] == WORKER_A


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_id_cleared_on_detect_crash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        assert resp["type"] == "command"

        assert_session_state(ctx["db_url"], session_id, worker_id=WORKER_A)

        set_session_fields(ctx["db_url"], session_id, command_at="2020-01-01T00:00:00Z")

        post_worker_sync(ctx["url"], "probe-worker")

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "crashed" or row["outcome"] == "failed", (
            f"Expected crashed or terminal failed, got executor_state={row['executor_state']}, outcome={row['outcome']}"
        )
        assert row["worker_id"] is None
        assert row["command_token"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_id_preserved_on_worker_reported_crash(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(session_id, "crashed"),
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "crashed"
        assert row["worker_id"] == WORKER_A


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_released_worker_ack_retry_harmless(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
            executor_state="idle",
            worker_id=None,
            command_token=None,
        )

        resp = post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack="stale-cancel-token",
        )

        assert resp["type"] == "no_action"

        assert_session_state(
            ctx["db_url"],
            session_id,
            executor_state="idle",
            worker_id=None,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_set_executor_state_rejected_on_finalized_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="completed",
            executor_state="idle",
            command_token=None,
        )

        resp = post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(session_id, "running"),
        )
        assert resp["type"] == "no_action"

        assert_session_state(ctx["db_url"], session_id, executor_state="idle")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_set_executor_state_accepted_during_cancel(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        set_session_fields(
            ctx["db_url"],
            session_id,
            desired="terminate",
            outcome="canceled",
            command_token="cancel-token-1",
            command_type="cancel",
            command_has_payload=False,
            command_at=datetime.utcnow().isoformat(),
        )

        resp = post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(session_id, "idle"),
            command_ack="cancel-token-1",
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["executor_state"] == "idle"
        assert row["command_token"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_recovery_budget_reset_on_idle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "work")

        resp = post_worker_sync(ctx["url"], WORKER_A)
        token = resp["token"]
        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=token,
        )

        set_session_fields(ctx["db_url"], session_id, recovery_attempts=2)

        row = get_session_row(ctx["db_url"], session_id)
        assert row["recovery_attempts"] == 2

        post_worker_sync(
            ctx["url"],
            WORKER_A,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(session_id, messages=[]),
        )

        row = get_session_row(ctx["db_url"], session_id)
        assert row["recovery_attempts"] == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_worker_no_sessions_gets_no_action(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = post_worker_sync(ctx["url"], "idle-worker-no-work")
        assert resp["type"] == "no_action"
