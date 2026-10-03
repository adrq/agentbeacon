# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import time

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
    start_worker,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    get_session_row,
    get_task_queue_count,
    seed_acp_mock_agent,
    seed_codex_test_agent,
)


_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_mock_sdks_dir = os.path.join(_project_root, "tests", "mock_sdks")
_EXECUTOR_ENV = {
    "AGENTBEACON_EXECUTORS_DIR": os.path.join(_mock_sdks_dir, "executors"),
    "NODE_OPTIONS": "--preserve-symlinks --preserve-symlinks-main",
}


def _poll_until(predicate, timeout=30, interval=0.5):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _session_agent_session_id(db_url, session_id):
    row = get_session_row(db_url, session_id)
    return row.get("agent_session_id")


def _session_executor_state(db_url, session_id):
    row = get_session_row(db_url, session_id)
    return row.get("executor_state")


def _agent_message_count(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = 'message'",
            (session_id,),
        ).fetchall()
    return sum(1 for (p,) in rows if json.loads(p).get("role") == "ROLE_AGENT")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_executes_acp_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello from e2e test"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Worker did not reach idle after turn completion"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate",
                timeout=10,
            )
            assert resp.status_code == 200

            time.sleep(3)
            assert worker.poll() is None, (
                "Worker should still be running after session terminate"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_multi_turn_acp_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "first turn"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Worker did not reach idle after first turn"

            assert _agent_message_count(ctx["db_url"], session_id) >= 1

            first_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "second turn prompt"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            assert _poll_until(
                lambda: _agent_message_count(ctx["db_url"], session_id) >= 2,
                timeout=30,
            ), "Worker did not complete second turn (expected 2 agent message events)"
            assert _agent_message_count(ctx["db_url"], session_id) == 2

            second_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert first_agent_sid == second_agent_sid, (
                f"agent_session_id changed between turns: {first_agent_sid} != {second_agent_sid}"
            )

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate",
                timeout=10,
            )
            assert resp.status_code == 200

            time.sleep(2)
            assert worker.poll() is None, "Worker should still be running"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_handles_session_complete_during_wait(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])

        exec_id_1, session_id_1 = create_execution_via_api(
            ctx["url"], agent_id, "first execution"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id_1) == "idle",
                timeout=30,
            ), "Worker did not reach idle for first execution"

            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id_1}/terminate",
                timeout=10,
            )
            assert resp.status_code == 200

            assert _poll_until(
                lambda: get_session_row(ctx["db_url"], session_id_1).get("worker_id")
                is None,
                timeout=30,
            ), "Worker was not released after terminate"

            exec_id_2, session_id_2 = create_execution_via_api(
                ctx["url"], agent_id, "second execution"
            )

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id_2) == "idle",
                timeout=60,
            ), "Worker did not reach idle for second execution"

            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id_2}/terminate",
                timeout=10,
            )
            assert resp.status_code == 200

            time.sleep(2)
            assert worker.poll() is None, "Worker should still be running"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_idle_no_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            time.sleep(3)
            assert worker.poll() is None, "Worker should stay alive with no sessions"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_stop_drains_queue_and_reaches_idle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="stop-buffered-sdk-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial long-running turn"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "running",
                timeout=30,
                interval=0.2,
            ), "Worker did not start the initial SDK turn"

            for prompt_text in ("buffered follow-up 1", "buffered follow-up 2"):
                resp = httpx.post(
                    f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                    json={"parts": [{"text": prompt_text}]},
                    timeout=10,
                )
                assert resp.status_code == 200, f"message push failed: {resp.text}"

            stop_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/stop",
                timeout=10,
            )
            assert stop_resp.status_code == 200, f"stop failed: {stop_resp.text}"

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
                interval=0.2,
            ), "Stopped session did not reach executor_state=idle after stop"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="stop",
                executor_state="idle",
            )

            time.sleep(1)
            assert get_task_queue_count(ctx["db_url"], session_id) == 0
            assert worker.poll() is None, "Worker should remain alive after stop-turn"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_bad_agent_config(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"],
            name="bad-agent",
            agent_type="acp",
        )

        bad_config = json.dumps(
            {
                "command": "/nonexistent/path/to/agent",
                "args": [],
                "timeout": 5,
            }
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE agents SET config = ? WHERE id = ?",
                (bad_config, agent_id),
            )
            conn.commit()

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "will fail"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: get_session_row(ctx["db_url"], session_id).get("outcome")
                == "failed",
                timeout=30,
                interval=0.5,
            ), "Session should reach outcome=failed after repeated startup failures"

            assert worker.poll() is None, "Worker should not crash on bad agent config"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="terminate",
                outcome="failed",
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("terminate_path", ["execution", "session"])
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_cancel_on_idle_executor_releases_worker(
    test_database, terminate_path, agent_type
):
    with scheduler_context(db_url=test_database) as ctx:
        if agent_type == "codex_sdk":
            agent_id = seed_codex_test_agent(ctx["db_url"], name="cancel-idle-agent")
        else:
            agent_id = seed_test_agent(
                ctx["db_url"], name="cancel-idle-agent", agent_type=agent_type
            )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "trigger idle cancel"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Worker did not reach idle"

            if terminate_path == "execution":
                resp = httpx.post(
                    f"{ctx['url']}/api/v1/executions/{exec_id}/terminate",
                    timeout=10,
                )
            else:
                resp = httpx.post(
                    f"{ctx['url']}/api/v1/sessions/{session_id}/terminate",
                    timeout=10,
                )
            assert resp.status_code == 200

            assert _poll_until(
                lambda: get_session_row(ctx["db_url"], session_id).get("worker_id")
                is None,
                timeout=15,
            ), "Worker was not released after cancel-on-idle"

            assert worker.poll() is None, "Worker should still be running"

            if agent_type != "codex_sdk":
                with db_conn(ctx["db_url"]) as conn:
                    crash_events = conn.execute(
                        "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                        (session_id,),
                    ).fetchall()
                crash_payloads = [
                    json.loads(p)
                    for (p,) in crash_events
                    if "crashed" in json.loads(p).get("executor_state", "")
                ]
                assert not crash_payloads, (
                    "Idle cancel should not produce crash state_change events. "
                    f"Found: {crash_payloads}"
                )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_worker_resume_without_payload_recovery(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        if agent_type == "codex_sdk":
            agent_id = seed_codex_test_agent(
                ctx["db_url"], name="resume-recovery-agent"
            )
        else:
            agent_id = seed_test_agent(
                ctx["db_url"], name="resume-recovery-agent", agent_type=agent_type
            )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial turn"
        )

        worker1 = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Worker did not reach idle after first turn"

            agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert agent_sid is not None, (
                "agent_session_id should be set after first turn"
            )
        finally:
            worker1.terminate()
            worker1.wait(timeout=5)

        time.sleep(0.5)

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET executor_state = 'crashed', "
                "worker_id = NULL, command_token = NULL, command_type = NULL, "
                "command_at = NULL, command_has_payload = FALSE "
                "WHERE id = ?",
                (session_id,),
            )
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        worker2 = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Resumed session did not reach idle"

            with db_conn(ctx["db_url"]) as conn:
                all_events = conn.execute(
                    "SELECT event_type, payload FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
            error_events = [
                (et, p) for et, p in all_events if '"error_kind":"executor_failed"' in p
            ]
            assert not error_events, (
                f"Resume-without-payload should not produce executor errors: {error_events}"
            )

            new_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert new_agent_sid is not None, (
                "agent_session_id should be set after resume"
            )
        finally:
            cleanup_processes([worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_worker_stop_resume_claude_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="stop-resume-claude", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "first turn"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "running",
                timeout=30,
                interval=0.2,
            ), "Worker did not start the initial SDK turn"

            stop_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/stop",
                timeout=10,
            )
            assert stop_resp.status_code == 200

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle"
                and get_session_row(ctx["db_url"], session_id).get("desired") == "stop",
                timeout=30,
                interval=0.2,
            ), "Session did not reach (stop, idle) after interrupting running turn"

            msg_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "resume after stop"}]},
                timeout=10,
            )
            assert msg_resp.status_code == 200

            msg_count_before = _agent_message_count(ctx["db_url"], session_id)
            assert _poll_until(
                lambda: _agent_message_count(ctx["db_url"], session_id)
                > msg_count_before
                and _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Worker did not complete second turn after resume"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            with db_conn(ctx["db_url"]) as conn:
                all_events = conn.execute(
                    "SELECT event_type, payload FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchall()

            crash_events = [
                (et, p)
                for et, p in all_events
                if et == "state_change" and '"executor_state":"crashed"' in p
            ]
            assert not crash_events, (
                f"Session should NOT have crashed during stop/resume, "
                f"but found crash events: {crash_events}"
            )

            payload_loss_events = [
                (et, p) for et, p in all_events if "message may have been lost" in p
            ]
            assert not payload_loss_events, (
                f"Session should NOT have payload-loss warnings during stop/resume, "
                f"but found: {payload_loss_events}"
            )
        finally:
            cleanup_processes([worker])
