# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import httpx
import pytest

from tests.testhelpers import (
    scheduler_context,
    create_execution_via_api,
    db_conn,
)
from tests.mock_agent_helpers import (
    seed_acp_mock_agent,
    get_session_row,
    set_session_fields,
    set_execution_fields,
    set_config,
    get_events,
    get_tasks,
    create_child_session_raw,
)

NO_WORKERS = {"AGENTBEACON_MAX_WORKERS": "0"}

both_backends = pytest.mark.parametrize(
    "test_database", ["sqlite", "postgres"], indirect=True
)


def _poll(predicate, timeout=15, interval=0.3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _drain(db_url, session_id):
    with db_conn(db_url) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
        conn.commit()


def _make_live_idle(db_url, session_id, drain=True):
    if drain:
        _drain(db_url, session_id)
    set_session_fields(
        db_url, session_id, executor_state="idle", agent_session_id="sdk-live"
    )


def _has_platform_message(db_url, session_id, fragment):
    for evt in get_events(db_url, session_id, "platform"):
        if fragment in (evt.get("payload") or ""):
            return True
    return False


@both_backends
def test_live_session_paused_on_restart(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="restart-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, session_id)

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        assert _poll(
            lambda: get_session_row(test_database, session_id)["desired"] == "stop"
        )
        row = get_session_row(test_database, session_id)
        assert row["desired"] == "stop"
        assert row["desired_by"] == "system:restart"
        assert row["command_token"] is None


@both_backends
def test_paused_session_resumes_on_message(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="resume-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, session_id)

    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx2:
        assert _poll(
            lambda: get_session_row(test_database, session_id)["desired"] == "stop"
        )
        resp = httpx.post(
            f"{ctx2['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": "resume please"}]},
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        row = get_session_row(test_database, session_id)
        assert row["desired"] == "run"
        assert row["desired_by"] == "user:send_message"


@both_backends
def test_drain_warning_only_on_nonempty_queue(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="drain-agent")
        _, sid_a = create_execution_via_api(ctx["url"], agent_id, "queued work")
        set_session_fields(
            test_database, sid_a, executor_state="idle", agent_session_id="sdk-a"
        )
        _, sid_b = create_execution_via_api(ctx["url"], agent_id, "no queue")
        _make_live_idle(test_database, sid_b, drain=True)

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        assert _poll(lambda: get_session_row(test_database, sid_a)["desired"] == "stop")
        assert get_session_row(test_database, sid_b)["desired"] == "stop"

        warnings_a = [
            e
            for e in get_events(test_database, sid_a, "platform")
            if "discarded on server restart" in (e.get("payload") or "")
        ]
        assert len(warnings_a) == 1, warnings_a
        assert (
            "1 queued message(s) discarded on server restart."
            in warnings_a[0]["payload"]
        )

        warnings_b = [
            e
            for e in get_events(test_database, sid_b, "platform")
            if "discarded on server restart" in (e.get("payload") or "")
        ]
        assert warnings_b == []


@both_backends
def test_flag_off_no_pause(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="flagoff-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, session_id)
        set_config(test_database, "restart.pause_sessions", "false")

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        time.sleep(2)
        row = get_session_row(test_database, session_id)
        assert row["desired"] == "run"
        assert row["desired_by"] != "system:restart"


@both_backends
def test_pause_leaves_recovery_attempts_unchanged(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="attempts-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, session_id)
        set_session_fields(test_database, session_id, recovery_attempts=0)

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        assert _poll(
            lambda: get_session_row(test_database, session_id)["desired"] == "stop"
        )
        row = get_session_row(test_database, session_id)
        assert row["recovery_attempts"] == 0


@both_backends
def test_new_session_after_boot_not_paused(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="post-boot-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        time.sleep(2)
        row = get_session_row(test_database, session_id)
        assert row["desired"] == "run"
        assert row["desired_by"] != "system:restart"


@both_backends
def test_pause_does_not_enqueue_child_results_to_parent(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="tree-agent")
        exec_id, parent_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, parent_id)
        child_id = create_child_session_raw(
            test_database,
            parent_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
        )

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        assert _poll(
            lambda: get_session_row(test_database, parent_id)["desired"] == "stop"
        )
        assert _poll(
            lambda: get_session_row(test_database, child_id)["desired"] == "stop"
        )
        parent_sources = [t.get("source") for t in get_tasks(test_database, parent_id)]
        assert f"child_result:{child_id}" not in parent_sources, parent_sources


@both_backends
def test_terminating_ancestor_descendant_not_paused(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="anc-agent")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, root_id)
        mid_id = create_child_session_raw(
            test_database,
            root_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
        )
        leaf_id = create_child_session_raw(
            test_database,
            mid_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
        )
        _drain(test_database, leaf_id)
        set_session_fields(test_database, mid_id, desired="terminate")

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        assert _poll(
            lambda: get_session_row(test_database, root_id)["desired"] == "stop"
        )
        leaf = get_session_row(test_database, leaf_id)
        assert leaf["desired_by"] != "system:restart"


@both_backends
def test_descendant_of_terminal_root_not_paused(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="repair-agent")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        child_id = create_child_session_raw(
            test_database,
            root_id,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
        )
        _drain(test_database, child_id)
        set_session_fields(
            test_database,
            root_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        set_execution_fields(test_database, exec_id, desired="run", outcome=None)

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        time.sleep(2)
        child = get_session_row(test_database, child_id)
        assert child["desired_by"] != "system:restart"


@both_backends
def test_in_flight_payload_warning_on_pause(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="payload-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _drain(test_database, session_id)
        set_session_fields(
            test_database,
            session_id,
            executor_state="idle",
            agent_session_id="sdk-p",
            command_has_payload=True,
        )

    with scheduler_context(db_url=test_database, env=NO_WORKERS):
        assert _poll(
            lambda: get_session_row(test_database, session_id)["desired"] == "stop"
        )
        assert _has_platform_message(
            test_database, session_id, "message may have been lost"
        )


@both_backends
def test_paused_unassigned_session_blocks_completion(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="paused-unassigned-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _drain(test_database, session_id)
        set_session_fields(
            test_database,
            session_id,
            executor_state="unassigned",
            agent_session_id="sdk-orphan",
        )

    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx2:
        assert _poll(
            lambda: get_session_row(test_database, session_id)["desired"] == "stop"
        )
        row = get_session_row(test_database, session_id)
        assert row["executor_state"] == "unassigned"
        assert row["agent_session_id"] == "sdk-orphan"

        sresp = httpx.get(
            f"{ctx2['url']}/api/v1/sessions/{session_id}", timeout=5
        ).json()
        assert sresp["status"] == "stopped"

        eresp = httpx.get(
            f"{ctx2['url']}/api/v1/executions/{exec_id}", timeout=5
        ).json()
        assert eresp["execution"]["completion_eligible"] is False

        term = httpx.post(
            f"{ctx2['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert term.status_code == 200, term.text
        assert _poll(
            lambda: get_session_row(test_database, session_id).get("outcome")
            is not None
        )
        assert get_session_row(test_database, session_id)["outcome"] == "canceled"


@both_backends
def test_failed_child_wakes_paused_parent(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="wake-agent")
        exec_a, parent_a = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, parent_a)
        failed_child = create_child_session_raw(
            test_database,
            parent_a,
            exec_a,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(
            test_database,
            failed_child,
            desired_by="system:crash",
            parent_notified=False,
        )
        _, parent_b = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, parent_b)

    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx2:
        assert _poll(
            lambda: get_session_row(test_database, parent_a)["desired"] == "stop"
        )
        assert get_session_row(test_database, parent_b)["desired"] == "stop"

        def parent_a_woke():
            httpx.post(
                f"{ctx2['url']}/api/worker/sync",
                json={"worker_id": "reconcile-trigger"},
                timeout=5,
            )
            return get_session_row(test_database, parent_a)["desired"] == "run"

        assert _poll(parent_a_woke, timeout=25, interval=0.5), "parent A was not woken"
        row_a = get_session_row(test_database, parent_a)
        assert row_a["desired"] == "run"
        assert row_a["desired_by"] == "system:child_crash_notify"
        assert get_session_row(test_database, failed_child)["parent_notified"] in (
            1,
            True,
        )
        parent_a_sources = [t.get("source") for t in get_tasks(test_database, parent_a)]
        assert f"child_result:{failed_child}" in parent_a_sources, parent_a_sources

        assert get_session_row(test_database, parent_b)["desired"] == "stop"


@both_backends
def test_continue_from_resumes_paused_parent(test_database):
    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx:
        agent_id = seed_acp_mock_agent(test_database, name="continue-agent")
        exec_id, parent_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, parent_id)
        child_id = create_child_session_raw(
            test_database,
            parent_id,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        set_session_fields(test_database, child_id, agent_session_id="sdk-child")
        _, other_id = create_execution_via_api(ctx["url"], agent_id, "hello")
        _make_live_idle(test_database, other_id)

    with scheduler_context(db_url=test_database, env=NO_WORKERS) as ctx2:
        assert _poll(
            lambda: get_session_row(test_database, parent_id)["desired"] == "stop"
        )
        assert get_session_row(test_database, other_id)["desired"] == "stop"

        resp = httpx.post(
            f"{ctx2['url']}/api/v1/sessions/{child_id}/continue",
            json={"parts": [{"text": "keep going"}]},
            timeout=5,
        )
        assert resp.status_code == 201, resp.text
        new_sid = resp.json()["session_id"]

        assert _poll(
            lambda: get_session_row(test_database, parent_id)["desired"] == "run"
        )
        assert (
            get_session_row(test_database, parent_id)["desired_by"]
            == "system:continue_from_notify"
        )
        parent_sources = [t.get("source") for t in get_tasks(test_database, parent_id)]
        assert f"child_result:{new_sid}" in parent_sources, parent_sources
        assert get_session_row(test_database, other_id)["desired"] == "stop"
