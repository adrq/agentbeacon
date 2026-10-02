# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest

from tests.mock_agent_helpers import (
    assert_no_command,
    assert_session_state,
    get_session_row,
    insert_task,
    make_agent_message,
    make_executor_report,
    make_turn_result,
    set_session_fields,
    post_worker_sync,
)
from tests.testhelpers import (
    create_execution_via_api,
    scheduler_context,
    seed_test_agent,
)


LONG_POLL_TIMEOUT_SECS = 3


def _sync_with_elapsed(url: str, worker_id: str, **kwargs) -> tuple[dict, float]:
    start = time.monotonic()
    response = post_worker_sync(
        url, worker_id, timeout=LONG_POLL_TIMEOUT_SECS + 5, **kwargs
    )
    elapsed = time.monotonic() - start
    return response, elapsed


def _create_assigned_session(ctx: dict, worker_id: str) -> tuple[str, str, str]:
    agent_id = seed_test_agent(ctx["db_url"], name="flush-test-agent")
    execution_id, session_id = create_execution_via_api(
        ctx["url"], agent_id, "flush test"
    )

    response = post_worker_sync(ctx["url"], worker_id)
    assert response["type"] == "command"
    assert response["action"]["type"] == "assign"
    assert response["action"]["session_id"] == session_id

    return execution_id, session_id, response["token"]


def _create_running_session(ctx: dict, worker_id: str) -> tuple[str, str]:
    execution_id, session_id, assign_token = _create_assigned_session(ctx, worker_id)

    response = post_worker_sync(
        ctx["url"],
        worker_id,
        executor_report=make_executor_report(
            session_id, "running", agent_session_id="sdk-1"
        ),
        command_ack=assign_token,
    )
    assert response["type"] == "no_action"
    assert_session_state(
        ctx["db_url"],
        session_id,
        executor_state="running",
        agent_session_id="sdk-1",
    )
    assert_no_command(ctx["db_url"], session_id)
    return execution_id, session_id


def _wait_for_agent_session_id(
    db_url: str, session_id: str, expected_value: str, timeout_secs: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        row = get_session_row(db_url, session_id)
        if row["agent_session_id"] == expected_value:
            return
        time.sleep(0.05)
    row = get_session_row(db_url, session_id)
    raise AssertionError(
        f"agent_session_id for session {session_id} did not become "
        f"{expected_value!r}; last value was {row['agent_session_id']!r}"
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_flush_command_ack_returns_immediately(test_database):
    worker_id = "flush-ack-worker"
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": str(LONG_POLL_TIMEOUT_SECS)}

    with scheduler_context(db_url=test_database, env=env) as ctx:
        _, session_id, assign_token = _create_assigned_session(ctx, worker_id)

        response, elapsed = _sync_with_elapsed(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(
                session_id, "running", agent_session_id="sdk-1"
            ),
            command_ack=assign_token,
        )

        assert response["type"] == "no_action"
        assert elapsed < 2
        assert_session_state(
            ctx["db_url"],
            session_id,
            executor_state="running",
            agent_session_id="sdk-1",
        )
        assert_no_command(ctx["db_url"], session_id)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_flush_turn_result_returns_immediately(test_database):
    worker_id = "flush-turn-result-worker"
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": str(LONG_POLL_TIMEOUT_SECS)}

    with scheduler_context(db_url=test_database, env=env) as ctx:
        _, session_id = _create_running_session(ctx, worker_id)

        response, elapsed = _sync_with_elapsed(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(
                session_id,
                messages=[make_agent_message("turn complete")],
            ),
        )

        assert response["type"] == "no_action"
        assert elapsed < 2
        assert_session_state(ctx["db_url"], session_id, executor_state="idle")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_non_flush_executor_report_long_polls(test_database):
    worker_id = "non-flush-worker"
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": str(LONG_POLL_TIMEOUT_SECS)}

    with scheduler_context(db_url=test_database, env=env) as ctx:
        _, session_id = _create_running_session(ctx, worker_id)

        response, elapsed = _sync_with_elapsed(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "running"),
        )

        assert response["type"] == "no_action"
        assert elapsed >= LONG_POLL_TIMEOUT_SECS
        assert elapsed < LONG_POLL_TIMEOUT_SECS + 2


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_sync_flush_still_delivers_pending_command(test_database):
    worker_id = "flush-command-worker"
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": str(LONG_POLL_TIMEOUT_SECS)}

    with scheduler_context(db_url=test_database, env=env) as ctx:
        execution_id, session_id = _create_running_session(ctx, worker_id)
        insert_task(ctx["db_url"], execution_id, session_id, "follow-up task")

        response, elapsed = _sync_with_elapsed(
            ctx["url"],
            worker_id,
            executor_report=make_executor_report(session_id, "idle"),
            turn_result=make_turn_result(
                session_id,
                messages=[make_agent_message("turn complete")],
            ),
        )

        assert response["type"] == "command"
        assert response["action"]["type"] == "feed_turn"
        assert elapsed < 2


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_worker_sync_stale_worker_race_does_not_assign_unrelated_work(test_database):
    worker_id = "stale-worker-race"
    env = {"AGENTBEACON_LONG_POLL_TIMEOUT_SECS": str(LONG_POLL_TIMEOUT_SECS)}

    with scheduler_context(db_url=test_database, env=env) as ctx:
        execution_id, session_id = _create_running_session(ctx, worker_id)

        next_agent_id = seed_test_agent(ctx["db_url"], name="flush-race-next-agent")
        _, next_session_id = create_execution_via_api(
            ctx["url"], next_agent_id, "queued while old session finalizes"
        )

        lock_conn = psycopg2.connect(ctx["db_url"])
        try:
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM executions WHERE id = %s FOR UPDATE",
                    (execution_id,),
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    post_worker_sync,
                    ctx["url"],
                    worker_id,
                    executor_report=make_executor_report(
                        session_id,
                        "running",
                        agent_session_id="sdk-race",
                    ),
                    timeout=LONG_POLL_TIMEOUT_SECS + 5,
                )

                _wait_for_agent_session_id(
                    ctx["db_url"], session_id, "sdk-race", timeout_secs=5.0
                )

                set_session_fields(ctx["db_url"], session_id, worker_id=None)

                lock_conn.commit()

                response = future.result(timeout=LONG_POLL_TIMEOUT_SECS + 5)

            assert response == {"type": "no_action"}
            assert_session_state(
                ctx["db_url"],
                session_id,
                executor_state="running",
                agent_session_id="sdk-race",
                worker_id=None,
            )
            assert_no_command(ctx["db_url"], next_session_id)
            next_row = get_session_row(ctx["db_url"], next_session_id)
            assert next_row["worker_id"] is None
        finally:
            lock_conn.close()
