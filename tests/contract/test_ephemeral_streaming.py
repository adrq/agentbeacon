# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import time

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
    seed_acp_mock_agent,
    seed_acp_scenario_agent,
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


def _session_status(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT desired, executor_state, outcome FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    desired, executor_state, outcome = row
    if outcome is not None:
        return outcome
    if executor_state == "idle":
        return "input-required"
    if executor_state == "running":
        return "working"
    return "submitted"


def _get_message_events(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT msg_seq, payload FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY msg_seq",
            (session_id,),
        ).fetchall()
    return [(seq, json.loads(payload)) for seq, payload in rows]


def _get_text_parts(db_url, session_id):
    events = _get_message_events(db_url, session_id)
    texts = []
    for seq, payload in events:
        parts = payload.get("parts", [])
        for part in parts:
            if "text" in part:
                texts.append((seq, part.get("text", "")))
    return texts


def _all_events_count(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT count(*) FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0]


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_showcase_no_duplicate_text_in_db(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_scenario_agent(
            ctx["db_url"], name="showcase", scenario="showcase"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test showcase"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=30,
            ), "Showcase execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            texts = [t for _, t in text_parts]

            for i, t1 in enumerate(texts):
                for j, t2 in enumerate(texts):
                    if i != j and len(t1) > 10 and t1 in t2:
                        pytest.fail(
                            f"Text at msg_seq {text_parts[i][0]} is substring of "
                            f"text at msg_seq {text_parts[j][0]}: "
                            f"'{t1[:50]}...' found in '{t2[:50]}...'"
                        )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_showcase_complete_text_preserved(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_scenario_agent(
            ctx["db_url"], name="showcase", scenario="showcase"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test showcase"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=30,
            ), "Showcase execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            assert len(text_parts) >= 1, "Expected at least one text part in DB"

            final_text = text_parts[-1][1]
            assert len(final_text) > 20, (
                f"Final text too short, may be truncated: '{final_text}'"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_demo_agent_persists_text_parts(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello demo"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=30,
            ), "Demo execution did not reach input-required"

            events = _get_message_events(ctx["db_url"], session_id)
            agent_texts = [
                part["text"]
                for _, payload in events
                if payload.get("role") == "ROLE_AGENT"
                for part in payload.get("parts", [])
                if "text" in part
            ]
            assert agent_texts == ["Mock ACP response: hello demo"], events
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sdk_agent_no_delta_events_in_db(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="sdk-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test sdk streaming"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=45,
            ), "SDK execution did not reach input-required"

            all_events = _get_message_events(ctx["db_url"], session_id)
            for seq, payload in all_events:
                for part in payload.get("parts", []):
                    data = part.get("data", {})
                    if isinstance(data, dict):
                        block_type = data.get("type", "")
                        assert block_type != "text_delta", (
                            f"text_delta block found in DB at msg_seq {seq}: {data}"
                        )
                        assert block_type != "thinking_delta", (
                            f"thinking_delta block found in DB at msg_seq {seq}: {data}"
                        )
                    part_type = part.get("type", "")
                    assert part_type not in ("text_delta", "thinking_delta"), (
                        f"Delta part type '{part_type}' found in DB at msg_seq {seq}"
                    )

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            texts = [t for _, t in text_parts]
            assert any(
                "I'll start by reading the configuration file" in t for t in texts
            ), f"Expected API call 1 text not found in DB texts: {texts}"
            assert any("Now searching for TODO/FIXME items" in t for t in texts), (
                f"Expected API call 2 text not found in DB texts: {texts}"
            )
            assert any(
                "Changes Complete" in t and "Fixed both issues" in t for t in texts
            ), f"Expected final summary text not found in DB texts: {texts}"

            for i, t1 in enumerate(texts):
                for j, t2 in enumerate(texts):
                    if i != j and len(t1) > 10 and t1 in t2:
                        pytest.fail(
                            f"Possible delta+complete overlap: text at msg_seq "
                            f"{text_parts[i][0]} is substring of text at "
                            f"msg_seq {text_parts[j][0]}"
                        )

            total_events = _all_events_count(ctx["db_url"], session_id)
            assert total_events == 15, (
                f"Expected exactly 15 events, got {total_events} — "
                f"deltas may be leaking to DB or events are missing"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sdk_agent_complete_text_preserved(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="sdk-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test sdk complete text"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=45,
            ), "SDK execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            assert len(text_parts) >= 1, "Expected at least one text part in DB"

            final_text = text_parts[-1][1]
            assert len(final_text) > 20, (
                f"Final text too short, may be truncated: '{final_text[:50]}'"
            )
            assert "Changes" in final_text or "Refactoring" in final_text, (
                f"Final text doesn't look like a complete message: '{final_text[:80]}'"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_copilot_sdk_agent_no_delta_events_in_db(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="copilot-agent", agent_type="copilot_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test copilot streaming"
        )

        worker = start_worker(ctx["url"], interval="500ms", extra_env=_EXECUTOR_ENV)
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=45,
            ), "Copilot execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            texts = [t for _, t in text_parts]

            for i, t1 in enumerate(texts):
                for j, t2 in enumerate(texts):
                    if i != j and len(t1) > 10 and t1 in t2:
                        pytest.fail(
                            f"Possible delta+complete overlap in Copilot: text at "
                            f"msg_seq {text_parts[i][0]} is substring of text at "
                            f"msg_seq {text_parts[j][0]}"
                        )

            all_events = _get_message_events(ctx["db_url"], session_id)
            has_data_parts = any(
                any("data" in p for p in payload.get("parts", []))
                for _, payload in all_events
            )
            assert has_data_parts, (
                "Expected data parts (tool_use/thinking) in persisted events"
            )
        finally:
            cleanup_processes([worker])
