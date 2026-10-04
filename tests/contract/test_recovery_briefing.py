# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import queue
import subprocess
import threading
import time

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
    start_worker,
)
from tests.mock_agent_helpers import (
    create_child_session_raw,
    get_session_row,
    make_executor_report,
    seed_codex_test_agent,
    set_session_fields,
    post_worker_sync,
)


_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_mock_sdks_dir = os.path.join(_project_root, "tests", "mock_sdks")
_EXECUTOR_ENV = {
    "AGENTBEACON_EXECUTORS_DIR": os.path.join(_mock_sdks_dir, "executors"),
    "NODE_OPTIONS": "--preserve-symlinks --preserve-symlinks-main",
}


def _require_executors_built(agent_type):
    assert agent_type == "codex_sdk" or os.path.exists(
        os.path.join(_mock_sdks_dir, "executors", "claude-executor.js")
    ), "mock_sdks/executors not built -- run 'make mock-sdks' first"


_BRIEFING_MARKER = "# AgentBeacon Environment"


def _resolve_data_dir():
    data_dir = os.environ.get("AGENTBEACON_DATA_DIR")
    if data_dir:
        return data_dir
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "agentbeacon")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "agentbeacon")


def _codex_config_toml_has_briefing(session_id):
    import tomllib

    data_dir = _resolve_data_dir()
    config_path = os.path.join(
        data_dir, "codex-state", session_id, ".codex", "config.toml"
    )
    if not os.path.exists(config_path):
        return False
    with open(config_path, "rb") as f:
        config_data = tomllib.load(f)
    instructions = config_data.get("developer_instructions", "")
    return _BRIEFING_MARKER in instructions


def _get_task_payloads(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _briefing_in_assign(response: dict) -> bool:
    action = response.get("action", {})
    agent_config = action.get("agent_config", {})
    system_prompt = agent_config.get("system_prompt", "")
    return _BRIEFING_MARKER in system_prompt


def _wait_for_recovery(url, db_url, session_id, timeout=8, interval=0.15):
    baseline = get_session_row(db_url, session_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = get_session_row(db_url, session_id)
        if current["executor_state"] != baseline["executor_state"]:
            return current
        if current.get("outcome") is not None and baseline.get("outcome") is None:
            return current
        post_worker_sync(url, worker_id="reconcile-trigger")
        time.sleep(interval)
    return get_session_row(db_url, session_id)


def _crash_session(ctx, session_id, worker_id="w1"):
    post_worker_sync(
        ctx["url"],
        worker_id,
        executor_report=make_executor_report(session_id, "crashed"),
    )


def _setup_resumable_sdk_session(ctx, agent_type):
    agent_id = seed_test_agent(
        ctx["db_url"], name=f"briefing-{agent_type}", agent_type=agent_type
    )
    exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "initial task")

    response = post_worker_sync(ctx["url"], "w1")
    assert response["type"] == "command"
    assert response["action"]["type"] == "assign"

    set_session_fields(
        ctx["db_url"],
        session_id,
        agent_session_id="sdk-session-abc",
        cwd="/tmp/test-workspace",
    )
    return agent_id, exec_id, session_id, response


def _wait_for_state(db_url, session_id, executor_state, timeout=30, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = get_session_row(db_url, session_id)
        if row.get("executor_state") == executor_state:
            return row
        time.sleep(interval)
    return get_session_row(db_url, session_id)


def _collect_worker_lines(proc, stop_event, line_queue):
    try:
        for line in proc.stdout:
            line_queue.put(line.rstrip())
            if stop_event.is_set():
                break
    except ValueError:
        pass


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_fresh_assign_includes_briefing_in_agent_config(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name=f"fresh-{agent_type}", agent_type=agent_type
        )
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "hello")

        payloads = _get_task_payloads(ctx["db_url"], session_id)
        for p in payloads:
            assert "agent_config" not in p, (
                f"SDK task_queue payload must not contain agent_config for {agent_type}"
            )

        response = post_worker_sync(ctx["url"], "w1")

        assert response["type"] == "command"
        action = response["action"]
        assert action["type"] == "assign"
        assert action["resume"] is False
        assert action["payload"] is not None

        assert "agent_config" in action, "Assign must carry agent_config"
        agent_config = action["agent_config"]
        assert "system_prompt" in agent_config, (
            "agent_config must contain system_prompt"
        )
        assert _BRIEFING_MARKER in agent_config["system_prompt"], (
            f"Fresh assign system_prompt missing briefing for {agent_type}"
        )

        driver = action.get("driver", {})
        driver_config = driver.get("config", {})
        assert "system_prompt" not in driver_config, (
            "driver.config must not carry system_prompt (only agent_config should)"
        )
        assert _BRIEFING_MARKER not in json.dumps(driver_config), (
            "driver.config must not contain briefing content"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_recovery_assign_includes_briefing_in_agent_config(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, session_id, _initial = _setup_resumable_sdk_session(
            ctx, agent_type
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
            conn.commit()

        _crash_session(ctx, session_id)

        recovered = _wait_for_recovery(ctx["url"], ctx["db_url"], session_id)
        assert recovered["executor_state"] == "unassigned", (
            f"Expected unassigned after recovery, got: {recovered['executor_state']}"
        )
        assert recovered["recovery_attempts"] == 1

        recovery_response = post_worker_sync(ctx["url"], "w1")

        assert recovery_response["type"] == "command"
        action = recovery_response["action"]
        assert action["type"] == "assign"
        assert action["resume"] is True, "Recovery assign must have resume=True"
        assert action["payload"] is None, (
            "Resume-without-payload must have payload=None"
        )

        assert "agent_config" in action, "Recovery assign is missing agent_config"
        agent_config = action["agent_config"]
        assert "system_prompt" in agent_config, (
            "agent_config must contain system_prompt on recovery"
        )
        assert _BRIEFING_MARKER in agent_config["system_prompt"], (
            f"Recovery assign system_prompt missing briefing for {agent_type}"
        )

        driver = action.get("driver", {})
        driver_config = driver.get("config", {})
        assert "system_prompt" not in driver_config, (
            "driver.config must not carry system_prompt on recovery assign"
        )
        assert _BRIEFING_MARKER not in json.dumps(driver_config), (
            "driver.config must not contain briefing content on recovery assign"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_recovery_with_payload_includes_briefing_in_agent_config(
    test_database, agent_type
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name=f"recovery-payload-{agent_type}", agent_type=agent_type
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial task"
        )

        post_worker_sync(ctx["url"], "w1")
        set_session_fields(
            ctx["db_url"],
            session_id,
            agent_session_id="sdk-session-def",
            cwd="/tmp/workspace",
        )

        with db_conn(ctx["db_url"]) as conn:
            payload_json = json.dumps(
                {
                    "agent_id": agent_id,
                    "message": {"role": "ROLE_USER", "parts": [{"text": "follow-up"}]},
                }
            )
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload, queued_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (exec_id, session_id, payload_json),
            )
            conn.commit()

        _crash_session(ctx, session_id)
        recovered = _wait_for_recovery(ctx["url"], ctx["db_url"], session_id)
        assert recovered["executor_state"] == "unassigned"

        recovery_payloads = _get_task_payloads(ctx["db_url"], session_id)
        for p in recovery_payloads:
            assert "agent_config" not in p, (
                f"SDK recovery task_queue payload must not contain agent_config for {agent_type}"
            )

        recovery_response = post_worker_sync(ctx["url"], "w1")
        assert recovery_response["type"] == "command"
        action = recovery_response["action"]
        assert action["type"] == "assign"
        assert action["resume"] is True
        assert action["payload"] is not None, "Expected payload in resume-with-payload"

        assert "agent_config" in action
        system_prompt = action["agent_config"].get("system_prompt", "")
        assert _BRIEFING_MARKER in system_prompt, (
            f"Resume-with-payload assign missing briefing for {agent_type}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_continue_from_first_assign_includes_briefing_in_agent_config(
    test_database, agent_type
):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name=f"continue-{agent_type}", agent_type=agent_type
        )
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "root start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(
            ctx["db_url"],
            child_sid,
            agent_session_id="sdk-orig-session",
            cwd="/tmp/workspace",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_sid}/continue",
            json={"parts": [{"text": "continue working"}]},
            timeout=5,
        )
        assert resp.status_code in (200, 201), f"continue_from failed: {resp.text}"
        new_session_id = resp.json()["session_id"]
        assert new_session_id != child_sid

        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_sid,))
            conn.commit()

        new_assign = None
        for _attempt in range(5):
            new_assign = post_worker_sync(ctx["url"], "w-new")
            if (
                new_assign["type"] == "command"
                and new_assign["action"].get("session_id") == new_session_id
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail(
                f"New session {new_session_id} was never assigned after recovery. "
                f"Last sync response: {new_assign}"
            )

        action = new_assign["action"]
        assert action["type"] == "assign"
        assert action["session_id"] == new_session_id

        assert "agent_config" in action, "continue_from assign must carry agent_config"
        system_prompt = action["agent_config"].get("system_prompt", "")
        assert _BRIEFING_MARKER in system_prompt, (
            f"continue_from assign missing briefing for {agent_type}"
        )

        new_row = get_session_row(ctx["db_url"], new_session_id)
        new_slug = new_row.get("slug", "")
        assert new_slug, "the new session has no slug"
        assert new_slug in system_prompt, (
            f"Briefing should reflect new session slug '{new_slug}', not old session"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_delegate_payload_no_agent_config_for_sdk(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(
            ctx["db_url"], name=f"lead-{agent_type}", agent_type=agent_type
        )
        child_agent_id = seed_test_agent(
            ctx["db_url"], name=f"child-{agent_type}", agent_type=agent_type
        )
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
            {"agent": f"child-{agent_type}", "prompt": "do work"},
        )
        child_payload = json.loads(result["content"][0]["text"])
        child_session_id = child_payload["session_id"]

        payloads = _get_task_payloads(ctx["db_url"], child_session_id)
        assert payloads, "Expected at least one task_queue entry for delegated session"
        for p in payloads:
            assert "agent_config" not in p, (
                f"SDK delegate task_queue payload must not contain agent_config for {agent_type}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_double_recovery_assign_includes_briefing_each_time(test_database, agent_type):
    with scheduler_context(db_url=test_database) as ctx:
        _agent_id, _exec_id, session_id, initial_response = (
            _setup_resumable_sdk_session(ctx, agent_type)
        )

        for recovery_num in range(1, 3):
            with db_conn(ctx["db_url"]) as conn:
                conn.execute(
                    "DELETE FROM task_queue WHERE session_id = ?", (session_id,)
                )
                conn.commit()

            _crash_session(ctx, session_id)
            recovered = _wait_for_recovery(ctx["url"], ctx["db_url"], session_id)
            assert recovered["executor_state"] == "unassigned", (
                f"Recovery #{recovery_num}: expected unassigned, got {recovered['executor_state']}"
            )

            recovery_response = post_worker_sync(ctx["url"], "w1")
            assert recovery_response["type"] == "command", (
                f"Recovery #{recovery_num}: expected command, got {recovery_response['type']}"
            )
            action = recovery_response["action"]
            assert action["type"] == "assign"
            assert action["resume"] is True

            agent_config = action.get("agent_config", {})
            system_prompt = agent_config.get("system_prompt", "")
            assert _BRIEFING_MARKER in system_prompt, (
                f"Recovery #{recovery_num} for {agent_type}: briefing missing from agent_config"
            )

            occurrences = system_prompt.count(_BRIEFING_MARKER)
            assert occurrences == 1, (
                f"Recovery #{recovery_num}: briefing appears {occurrences} times, expected 1"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_full_stack_recovery_mock_sdk_has_briefing(test_database, agent_type):
    _require_executors_built(agent_type)

    with scheduler_context(db_url=test_database) as ctx:
        if agent_type == "codex_sdk":
            agent_id = seed_codex_test_agent(
                ctx["db_url"], name=f"fullstack-{agent_type}"
            )
        else:
            agent_id = seed_test_agent(
                ctx["db_url"],
                name=f"fullstack-{agent_type}",
                agent_type=agent_type,
            )
        _exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial task"
        )

        line_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        worker = start_worker(
            ctx["url"],
            interval="500ms",
            extra_env={
                **_EXECUTOR_ENV,
                "RUST_LOG": "claude_executor=debug,copilot_executor=debug,codex_executor=debug,info",
            },
        )
        try:
            reader_thread = threading.Thread(
                target=_collect_worker_lines,
                args=(worker, stop_event, line_queue),
                daemon=True,
            )
            reader_thread.start()

            first_idle = _wait_for_state(ctx["db_url"], session_id, "idle", timeout=30)
            assert first_idle["executor_state"] == "idle", (
                "Session never reached idle after first turn"
            )

            if agent_type == "codex_sdk":
                assert _codex_config_toml_has_briefing(session_id), (
                    "Codex config.toml missing briefing in developer_instructions. "
                    "The briefing did not reach the Codex executor boundary."
                )
            else:
                time.sleep(0.5)
                lines = []
                while not line_queue.empty():
                    lines.append(line_queue.get_nowait())

                sdk_name = (
                    "mock-claude-sdk"
                    if agent_type == "claude_sdk"
                    else "mock-copilot-sdk"
                )
                briefing_present_lines = [
                    line
                    for line in lines
                    if "system_prompt_has_briefing=true" in line and sdk_name in line
                ]
                assert briefing_present_lines, (
                    f"Mock {sdk_name} never logged system_prompt_has_briefing=true. "
                    f"This means the briefing did not reach the SDK executor boundary. "
                    f"Worker lines:\n" + "\n".join(lines[-40:])
                )

        finally:
            stop_event.set()
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_full_stack_multi_turn_briefing_stable(test_database, agent_type):
    _require_executors_built(agent_type)

    with scheduler_context(db_url=test_database) as ctx:
        if agent_type == "codex_sdk":
            agent_id = seed_codex_test_agent(
                ctx["db_url"], name=f"multi-turn-{agent_type}"
            )
        else:
            agent_id = seed_test_agent(
                ctx["db_url"],
                name=f"multi-turn-{agent_type}",
                agent_type=agent_type,
            )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial task"
        )

        line_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        worker = start_worker(
            ctx["url"],
            interval="500ms",
            extra_env={
                **_EXECUTOR_ENV,
                "RUST_LOG": "claude_executor=debug,copilot_executor=debug,codex_executor=debug,info",
            },
        )
        try:
            reader_thread = threading.Thread(
                target=_collect_worker_lines,
                args=(worker, stop_event, line_queue),
                daemon=True,
            )
            reader_thread.start()

            assert (
                _wait_for_state(ctx["db_url"], session_id, "idle", timeout=30)[
                    "executor_state"
                ]
                == "idle"
            ), "Did not reach idle on first turn"

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "follow-up question"}]},
                timeout=5,
            )
            assert resp.status_code in (200, 201, 202), (
                f"send message failed: {resp.text}"
            )

            _wait_for_state(ctx["db_url"], session_id, "idle", timeout=30)

            if agent_type == "codex_sdk":
                assert _codex_config_toml_has_briefing(session_id), (
                    "Codex config.toml missing briefing in developer_instructions "
                    "after multi-turn. Briefing did not survive across turns."
                )
            else:
                lines = []
                while not line_queue.empty():
                    lines.append(line_queue.get_nowait())

                sdk_name = (
                    "mock-claude-sdk"
                    if agent_type == "claude_sdk"
                    else "mock-copilot-sdk"
                )
                briefing_lines = [
                    line
                    for line in lines
                    if "system_prompt_has_briefing=true" in line and sdk_name in line
                ]
                assert briefing_lines, (
                    f"Mock {sdk_name} never logged system_prompt_has_briefing=true. "
                    f"Worker lines:\n" + "\n".join(lines[-40:])
                )

        finally:
            stop_event.set()
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("agent_type", ["claude_sdk", "copilot_sdk", "codex_sdk"])
def test_full_stack_crash_recovery_mock_sdk_has_briefing_and_resume(
    test_database, agent_type
):
    _require_executors_built(agent_type)

    sdk_name = {
        "claude_sdk": "mock-claude-sdk",
        "copilot_sdk": "mock-copilot-sdk",
    }.get(agent_type)
    worker_env = {
        **_EXECUTOR_ENV,
        "RUST_LOG": "claude_executor=debug,copilot_executor=debug,codex_executor=debug,info",
    }

    with scheduler_context(db_url=test_database) as ctx:
        if agent_type == "codex_sdk":
            agent_id = seed_codex_test_agent(
                ctx["db_url"], name=f"crash-resume-fullstack-{agent_type}"
            )
        else:
            agent_id = seed_test_agent(
                ctx["db_url"],
                name=f"crash-resume-fullstack-{agent_type}",
                agent_type=agent_type,
            )
        _exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial task"
        )

        stop_a = threading.Event()
        queue_a: queue.Queue = queue.Queue()
        worker_a = start_worker(ctx["url"], interval="500ms", extra_env=worker_env)
        try:
            threading.Thread(
                target=_collect_worker_lines,
                args=(worker_a, stop_a, queue_a),
                daemon=True,
            ).start()

            first_idle = _wait_for_state(ctx["db_url"], session_id, "idle", timeout=30)
            assert first_idle["executor_state"] == "idle", (
                "Session never reached idle after first assign"
            )

            session_row = get_session_row(ctx["db_url"], session_id)
            assert session_row.get("agent_session_id"), (
                "agent_session_id not set after first run -- cannot test resume"
            )
        finally:
            stop_a.set()
            worker_a.terminate()
            try:
                worker_a.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_a.kill()
                worker_a.wait(timeout=3)

        time.sleep(0.3)

        set_session_fields(
            ctx["db_url"], session_id, executor_state="crashed", worker_id=None
        )

        stop_b = threading.Event()
        queue_b: queue.Queue = queue.Queue()
        worker_b = start_worker(ctx["url"], interval="500ms", extra_env=worker_env)
        try:
            threading.Thread(
                target=_collect_worker_lines,
                args=(worker_b, stop_b, queue_b),
                daemon=True,
            ).start()

            recovered = _wait_for_state(
                ctx["db_url"], session_id, "idle", timeout=30, interval=0.3
            )
            assert recovered["executor_state"] == "idle", (
                f"Session did not recover to idle after crash; "
                f"state={recovered['executor_state']}"
            )

            if agent_type == "codex_sdk":
                assert _codex_config_toml_has_briefing(session_id), (
                    "Codex config.toml missing briefing after crash recovery. "
                    "The briefing was lost across crash/recovery."
                )
            else:
                time.sleep(0.5)
                lines = []
                while not queue_b.empty():
                    lines.append(queue_b.get_nowait())

                resume_lines = [
                    line for line in lines if "resume=true" in line and sdk_name in line
                ]
                assert resume_lines, (
                    f"Mock {sdk_name} never logged resume=true after crash recovery. "
                    f"Worker lines:\n" + "\n".join(lines[-50:])
                )

                briefing_lines = [
                    line
                    for line in lines
                    if "system_prompt_has_briefing=true" in line and sdk_name in line
                ]
                assert briefing_lines, (
                    f"Mock {sdk_name} never logged system_prompt_has_briefing=true on recovery. "
                    "This means the briefing was lost across crash/recovery. "
                    f"Worker lines:\n" + "\n".join(lines[-50:])
                )

        finally:
            stop_b.set()
            cleanup_processes([worker_b])
