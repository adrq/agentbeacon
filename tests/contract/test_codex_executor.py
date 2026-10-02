# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import time

import tomllib

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


def _session_executor_state(db_url, session_id):
    row = get_session_row(db_url, session_id)
    return row.get("executor_state")


def _session_agent_session_id(db_url, session_id):
    row = get_session_row(db_url, session_id)
    return row.get("agent_session_id")


def _get_message_events(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT msg_seq, payload FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY msg_seq",
            (session_id,),
        ).fetchall()
    return [(seq, json.loads(payload)) for seq, payload in rows]


def _get_all_events(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [(et, json.loads(p)) for et, p in rows]


def _usage_events(db_url, session_id):
    events = _get_all_events(db_url, session_id)
    result = []
    for _et, payload in events:
        if _et != "message":
            continue
        for part in payload.get("parts", []):
            data = part.get("data")
            if (
                isinstance(data, dict)
                and data.get("method") == "thread/tokenUsage/updated"
            ):
                result.append(data)
    return result


def _codex_agent_message_events(db_url, session_id):
    events = _get_message_events(db_url, session_id)
    completed = []
    for seq, payload in events:
        for part in payload.get("parts", []):
            data = part.get("data")
            if not isinstance(data, dict):
                continue
            if data.get("method") == "item/completed":
                item = data.get("params", {}).get("item", {})
                if item.get("type") == "agentMessage":
                    completed.append((seq, item))
            elif data.get("type") == "agentMessage":
                completed.append((seq, data))
    return completed


def _codex_agent_message_count(db_url, session_id):
    return len(_codex_agent_message_events(db_url, session_id))


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_mock(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello from codex e2e test"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after turn completion"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert agent_sid is not None, (
                "agent_session_id should be set after codex turn (thread_id)"
            )
            assert agent_sid.startswith("thr_mock_"), (
                f"Expected mock thread_id prefix, got: {agent_sid}"
            )

            assert _poll_until(
                lambda: len(_codex_agent_message_events(ctx["db_url"], session_id))
                >= 1,
                timeout=10,
                interval=0.3,
            ), "Expected at least 1 item/completed event"
            completed = _codex_agent_message_events(ctx["db_url"], session_id)

            first_item = completed[0][1]
            item_text = first_item.get("text", "")
            assert item_text, f"item/completed should have text field: {first_item}"
            assert "hello from codex e2e test" in item_text, (
                f"Item text should echo prompt: {item_text}"
            )

            assert _poll_until(
                lambda: _usage_events(ctx["db_url"], session_id),
                timeout=10,
                interval=0.3,
            ), "Expected at least 1 thread/tokenUsage/updated event"

            for ue in _usage_events(ctx["db_url"], session_id):
                params = ue.get("params", {})
                assert params.get("threadId") is not None, (
                    f"thread/tokenUsage/updated must have params.threadId "
                    f"(schema: ThreadTokenUsageUpdatedNotification): {ue}"
                )
                assert params.get("turnId") is not None, (
                    f"thread/tokenUsage/updated must have params.turnId "
                    f"(schema: ThreadTokenUsageUpdatedNotification): {ue}"
                )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_streaming(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "streaming test prompt"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle for streaming test"

            assert _poll_until(
                lambda: len(_codex_agent_message_events(ctx["db_url"], session_id))
                >= 1,
                timeout=10,
                interval=0.3,
            ), "Expected at least 1 item/completed event after turn"
            completed = _codex_agent_message_events(ctx["db_url"], session_id)

            item = completed[0][1]
            full_text = item.get("text", "")
            assert "streaming test prompt" in full_text, (
                f"Persisted item text should contain full response, got: {full_text}"
            )

            all_events = _get_all_events(ctx["db_url"], session_id)
            for event_type, payload in all_events:
                if event_type == "message":
                    for part in payload.get("parts", []):
                        data = part.get("data")
                        if isinstance(data, dict):
                            method = data.get("method", "")
                            assert method != "item/agentMessage/delta", (
                                f"Delta events should not be persisted in DB: {data}"
                            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_stop_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "DELAY_5")

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "running",
                timeout=30,
                interval=0.2,
            ), "Codex session did not start running"

            stop_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/stop",
                timeout=10,
            )
            assert stop_resp.status_code == 200, f"Stop failed: {stop_resp.text}"

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
                interval=0.2,
            ), "Stopped codex session did not reach idle"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="stop",
                executor_state="idle",
            )

            assert _poll_until(
                lambda: any(
                    "stopped_by_user" in json.dumps(p)
                    for _et, p in _get_all_events(ctx["db_url"], session_id)
                    if _et == "platform"
                ),
                timeout=10,
                interval=0.3,
            ), "Expected platform event with stopped_by_user error_kind after stop turn"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_startup_failures(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"],
            name="codex-bad-cmd",
            agent_type="codex_sdk",
        )
        bad_config = json.dumps(
            {
                "command": "/nonexistent/path/to/codex-agent",
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
            ctx["url"], agent_id, "will fail at startup"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: get_session_row(ctx["db_url"], session_id).get("outcome")
                == "failed",
                timeout=60,
                interval=0.5,
            ), "Codex startup failure should reach outcome=failed"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="terminate",
                outcome="failed",
            )

            assert worker.poll() is None, (
                "Worker should not crash on codex startup failure"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_resume(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "initial turn for resume test"
        )

        worker1 = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after first turn"

            original_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert original_agent_sid is not None, (
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
                timeout=45,
            ), "Resumed codex session did not reach idle"

            resumed_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert resumed_agent_sid is not None, (
                "agent_session_id should be set after resume"
            )

            all_events = _get_all_events(ctx["db_url"], session_id)
            error_events = [
                (et, p)
                for et, p in all_events
                if '"error_kind":"executor_failed"' in json.dumps(p)
            ]
            assert not error_events, (
                f"Resume should not produce executor_failed errors: {error_events}"
            )
        finally:
            cleanup_processes([worker2])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_tool_blocking(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "config verification test"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle for config test"

            data_dir = os.environ.get(
                "AGENTBEACON_DATA_DIR",
                os.path.join(
                    os.environ.get(
                        "XDG_DATA_HOME",
                        os.path.join(os.path.expanduser("~"), ".local", "share"),
                    ),
                    "agentbeacon",
                ),
            )
            codex_home = os.path.join(data_dir, "codex-state", session_id, ".codex")
            config_path = os.path.join(codex_home, "config.toml")

            assert os.path.exists(config_path), (
                f"config.toml should exist at {config_path}"
            )

            with open(config_path, "rb") as f:
                config_data = tomllib.load(f)

            assert "mcp_servers" in config_data, (
                "config.toml should have mcp_servers section"
            )
            assert "agentbeacon" in config_data["mcp_servers"], (
                "config.toml should have agentbeacon MCP server"
            )
            ab_server = config_data["mcp_servers"]["agentbeacon"]
            assert "url" in ab_server, "agentbeacon MCP server should have url"
            assert ab_server.get("required") is True, (
                "agentbeacon MCP server should be required"
            )

            assert "sandbox_mode" in config_data, "config.toml should have sandbox_mode"

            assert "approval_policy" in config_data, (
                "config.toml should have approval_policy"
            )

            assert config_data.get("notice", {}).get("fast_default_opt_out") is True, (
                "config.toml should have notice.fast_default_opt_out = true"
            )
            assert config_data.get("features", {}).get("multi_agent") is False, (
                "config.toml should have features.multi_agent = false"
            )
            assert config_data.get("features", {}).get("fast_mode") is False, (
                "config.toml should have features.fast_mode = false"
            )

            assert config_data.get("service_tier") is None, (
                "config.toml should not set service_tier"
            )
        finally:
            cleanup_processes([worker])


def _load_codex_config_toml(session_id):
    data_dir = os.environ.get(
        "AGENTBEACON_DATA_DIR",
        os.path.join(
            os.environ.get(
                "XDG_DATA_HOME",
                os.path.join(os.path.expanduser("~"), ".local", "share"),
            ),
            "agentbeacon",
        ),
    )
    config_path = os.path.join(
        data_dir, "codex-state", session_id, ".codex", "config.toml"
    )
    assert os.path.exists(config_path), f"config.toml should exist at {config_path}"
    with open(config_path, "rb") as f:
        return tomllib.load(f)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_config_emits_reasoning_effort(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(
            ctx["db_url"], config_extra={"model_reasoning_effort": "high"}
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "reasoning effort test"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle for effort test"

            config_data = _load_codex_config_toml(session_id)
            assert config_data.get("model_reasoning_effort") == "high"

            assert config_data.get("notice", {}).get("fast_default_opt_out") is True
            assert config_data.get("features", {}).get("multi_agent") is False
            assert config_data.get("features", {}).get("fast_mode") is False
            assert config_data.get("service_tier") is None
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_config_omits_reasoning_effort_when_absent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "no effort test"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle for no-effort test"

            config_data = _load_codex_config_toml(session_id)
            assert "model_reasoning_effort" not in config_data

            assert config_data.get("notice", {}).get("fast_default_opt_out") is True
            assert config_data.get("features", {}).get("multi_agent") is False
            assert config_data.get("features", {}).get("fast_mode") is False
            assert config_data.get("service_tier") is None
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_multi_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "first codex turn"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after first turn"

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) >= 1,
                timeout=10,
            ), "No item/completed events after first turn reached idle"

            first_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "second codex turn"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) >= 2,
                timeout=30,
            ), "Codex worker did not complete second turn"

            second_agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert first_agent_sid == second_agent_sid, (
                f"agent_session_id changed between turns: "
                f"{first_agent_sid} != {second_agent_sid}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_failed_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "FAIL_TURN"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after failed turn"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            assert _poll_until(
                lambda: any(
                    "executor_failed" in json.dumps(p)
                    for _et, p in _get_all_events(ctx["db_url"], session_id)
                    if _et == "platform"
                ),
                timeout=10,
                interval=0.3,
            ), "Expected platform event with executor_failed error_kind for failed turn"

            all_events = _get_all_events(ctx["db_url"], session_id)
            platform_events = [p for et, p in all_events if et == "platform"]
            has_error_msg = any(
                "mock turn failure" in json.dumps(p) for p in platform_events
            )
            assert has_error_msg, (
                f"Expected error message in platform event, got: {platform_events}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_cancel_during_turn(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "DELAY_5")

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "running",
                timeout=30,
                interval=0.2,
            ), "Codex session did not start running"

            cancel_resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/terminate",
                timeout=10,
            )
            assert cancel_resp.status_code == 200, (
                f"Terminate failed: {cancel_resp.text}"
            )

            assert _poll_until(
                lambda: get_session_row(ctx["db_url"], session_id).get("outcome")
                is not None,
                timeout=30,
                interval=0.3,
            ), "Cancelled codex session did not reach terminal state"

            row = get_session_row(ctx["db_url"], session_id)
            assert row["outcome"] == "canceled", (
                f"Expected outcome=canceled, got {row['outcome']}"
            )

            assert worker.poll() is None, "Worker should not crash on session cancel"
        finally:
            cleanup_processes([worker])
