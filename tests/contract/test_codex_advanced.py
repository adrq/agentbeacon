# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import tempfile
import time

import httpx
import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
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


def _codex_agent_message_events(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT msg_seq, payload FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY id",
            (session_id,),
        ).fetchall()
    completed = []
    for seq, p in rows:
        payload = json.loads(p)
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


def _codex_item_texts(db_url, session_id):
    completed = _codex_agent_message_events(db_url, session_id)
    texts = []
    for _seq, item in completed:
        text = item.get("text", "")
        if text:
            texts.append(text)
    return texts


def _get_all_events(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [(et, json.loads(p)) for et, p in rows]


def _resolve_data_dir():
    data_dir = os.environ.get("AGENTBEACON_DATA_DIR")
    if data_dir:
        return data_dir
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "agentbeacon")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "agentbeacon")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_steer_success(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "DELAY_3")

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "running",
                timeout=30,
                interval=0.2,
            ), "Codex session did not start running"

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "steered follow-up"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after steered turn"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) >= 1,
                timeout=10,
                interval=0.3,
            ), "Expected at least 1 item/completed event"

            texts = _codex_item_texts(ctx["db_url"], session_id)
            assert len(texts) >= 1, (
                f"Expected item/completed with text content, got {len(texts)}"
            )

            combined = " ".join(texts)
            assert "DELAY_3" in combined or "steered" in combined.lower(), (
                f"Agent output should contain original or steered content: {texts}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_steer_fallback(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "DELAY_3")

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "running",
                timeout=30,
                interval=0.2,
            ), "Codex session did not start running"

            time.sleep(0.5)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "STEER_REJECT_NOT_STEERABLE"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after steer fallback"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) >= 2,
                timeout=10,
                interval=0.3,
            ), (
                f"Expected at least 2 item/completed events (original + fallback), "
                f"got {_codex_agent_message_count(ctx['db_url'], session_id)}"
            )

            texts = _codex_item_texts(ctx["db_url"], session_id)
            has_deferred_content = any("STEER_REJECT_NOT_STEERABLE" in t for t in texts)
            assert has_deferred_content, (
                f"Fallback turn should include deferred prompt content: {texts}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_steer_multiple_deferred(test_database):
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

            time.sleep(0.5)

            resp1 = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "STEER_REJECT_NOT_STEERABLE"}]},
                timeout=10,
            )
            assert resp1.status_code == 200, f"message push 1 failed: {resp1.text}"

            time.sleep(0.5)

            resp2 = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "STEER_REJECT_NOT_STEERABLE"}]},
                timeout=10,
            )
            assert resp2.status_code == 200, f"message push 2 failed: {resp2.text}"

            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle after multiple deferred prompts"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            assert _poll_until(
                lambda: _codex_agent_message_count(ctx["db_url"], session_id) >= 2,
                timeout=10,
                interval=0.3,
            ), (
                f"Expected at least 2 item/completed events, "
                f"got {_codex_agent_message_count(ctx['db_url'], session_id)}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_steer_stop_clears_deferred(test_database):
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

            time.sleep(0.5)

            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/message",
                json={"parts": [{"text": "STEER_REJECT_NOT_STEERABLE"}]},
                timeout=10,
            )
            assert resp.status_code == 200, f"message push failed: {resp.text}"

            time.sleep(1.0)

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

            completed_count = _codex_agent_message_count(ctx["db_url"], session_id)
            assert completed_count <= 1, (
                f"Expected at most 1 item/completed (no fallback turn after stop), "
                f"got {completed_count}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_codex_startup_partial_failure(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "FAIL_TURN_START"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "FAIL_TURN_START session should recover to idle"

            assert_session_state(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="idle",
            )

            agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert agent_sid is not None, (
                "agent_session_id should be set from thread/start even though "
                "the first turn/start failed"
            )
            assert agent_sid.startswith("thr_mock_"), (
                f"agent_session_id should be the mock thread_id, got: {agent_sid}"
            )

            all_events = _get_all_events(ctx["db_url"], session_id)
            recovery_events = [
                (et, p)
                for et, p in all_events
                if et == "state_change"
                and isinstance(p, dict)
                and p.get("recovery_attempt") is not None
            ]
            assert len(recovery_events) >= 1, (
                f"Expected at least 1 recovery state_change event, "
                f"got {len(recovery_events)}"
            )

            assert worker.poll() is None, (
                "Worker should not crash on partial startup failure"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_codex_continue_from_codex_home(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_codex_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "codex home test"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_executor_state(ctx["db_url"], session_id) == "idle",
                timeout=30,
            ), "Codex session did not reach idle"

            agent_sid = _session_agent_session_id(ctx["db_url"], session_id)
            assert agent_sid is not None, "agent_session_id should be set"
            assert agent_sid.startswith("thr_mock_"), (
                f"Expected mock thread_id, got: {agent_sid}"
            )

            data_dir = _resolve_data_dir()
            codex_home = os.path.join(data_dir, "codex-state", session_id, ".codex")
            assert os.path.isdir(codex_home), f"CODEX_HOME should exist at {codex_home}"

            config_path = os.path.join(codex_home, "config.toml")
            assert os.path.isfile(config_path), (
                f"config.toml should exist at {config_path}"
            )

            symlink_path = os.path.join(data_dir, "codex-state", agent_sid)
            assert os.path.islink(symlink_path), (
                f"Thread symlink should exist at {symlink_path}"
            )
            expected_target = os.path.join(data_dir, "codex-state", session_id)
            actual_target = os.readlink(symlink_path)
            assert actual_target == expected_target, (
                f"Symlink should point to {expected_target}, got {actual_target}"
            )

            resumed_codex_home = os.path.join(
                data_dir, "codex-state", agent_sid, ".codex"
            )
            assert os.path.isdir(resumed_codex_home), (
                f"Symlink-based CODEX_HOME should resolve to {resumed_codex_home}"
            )

            real_original = os.path.realpath(codex_home)
            real_resumed = os.path.realpath(resumed_codex_home)
            assert real_original == real_resumed, (
                f"Original and resumed CODEX_HOME should resolve to same directory: "
                f"{real_original} != {real_resumed}"
            )

            resumed_config = os.path.join(resumed_codex_home, "config.toml")
            assert os.path.isfile(resumed_config), (
                f"config.toml should be accessible through symlink at {resumed_config}"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_codex_auth_json_symlink(test_database):
    with tempfile.TemporaryDirectory() as fake_codex_home:
        fake_auth = os.path.join(fake_codex_home, "auth.json")
        with open(fake_auth, "w") as f:
            json.dump({"token": "fake-test-token"}, f)

        with scheduler_context(db_url=test_database) as ctx:
            agent_id = seed_codex_test_agent(ctx["db_url"])
            exec_id, session_id = create_execution_via_api(
                ctx["url"], agent_id, "auth symlink test"
            )

            worker = start_worker(
                ctx["url"],
                interval="500ms",
                extra_env={**_EXECUTOR_ENV, "CODEX_HOME": fake_codex_home},
            )
            try:
                assert _poll_until(
                    lambda: _session_executor_state(ctx["db_url"], session_id)
                    == "idle",
                    timeout=30,
                ), "Codex session did not reach idle"

                data_dir = _resolve_data_dir()
                codex_home = os.path.join(data_dir, "codex-state", session_id, ".codex")
                auth_link = os.path.join(codex_home, "auth.json")

                assert os.path.islink(auth_link), (
                    f"auth.json should be a symlink at {auth_link}"
                )
                assert os.readlink(auth_link) == fake_auth, (
                    f"auth.json symlink should point to {fake_auth}, "
                    f"got {os.readlink(auth_link)}"
                )

                with open(auth_link) as f:
                    content = json.load(f)
                assert content == {"token": "fake-test-token"}, (
                    f"Symlinked auth.json should contain original content, got {content}"
                )
            finally:
                cleanup_processes([worker])
