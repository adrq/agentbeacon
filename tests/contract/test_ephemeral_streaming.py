"""Tests for ephemeral streaming — delta events bypass DB, complete messages persist.

Verifies that text_delta (streaming) messages from SDK executors are NOT stored
in the database, while complete messages are persisted correctly. Also verifies
that non-streaming (ACP) agents are unaffected.
"""

import json
import os
import time

import pytest

from tests.testhelpers import (
    cleanup_processes,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_acp_mock_agent,
    seed_acp_scenario_agent,
    seed_test_agent,
    start_worker,
)

# Ensure SDK executors use mock implementations (no real API calls)
os.environ["AGENTBEACON_MOCK_SDK"] = "1"
# Point worker to built executor JS files
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
os.environ.setdefault(
    "AGENTBEACON_EXECUTORS_DIR", os.path.join(_project_root, "executors", "dist")
)


def _poll_until(predicate, timeout=30, interval=0.5):
    """Poll until predicate returns True or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _session_status(db_url, session_id):
    """Read session status from DB."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None


def _get_message_events(db_url, session_id):
    """Get all message events for a session."""
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT msg_seq, payload FROM events WHERE session_id = ? AND event_type = 'message' ORDER BY msg_seq",
            (session_id,),
        ).fetchall()
    return [(seq, json.loads(payload)) for seq, payload in rows]


def _get_text_parts(db_url, session_id):
    """Get all text parts from message events for a session."""
    events = _get_message_events(db_url, session_id)
    texts = []
    for seq, payload in events:
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("kind") == "text":
                texts.append((seq, part.get("text", "")))
    return texts


def _all_events_count(db_url, session_id):
    """Count all events for a session."""
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT count(*) FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0]


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_showcase_no_duplicate_text_in_db(test_database):
    """After showcase execution (ACP), DB text should not be doubled.

    The showcase ACP agent does not emit text_delta (it's ACP, not SDK),
    so all messages should persist normally — this verifies ACP is unaffected.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_scenario_agent(
            ctx["db_url"], name="showcase", scenario="showcase"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test showcase"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=30,
            ), "Showcase execution did not reach input-required"

            # Get text parts — each text should appear exactly once
            text_parts = _get_text_parts(ctx["db_url"], session_id)
            texts = [t for _, t in text_parts]

            # Verify no text is a prefix/substring of any other text
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
    """Complete text messages are fully preserved in DB after showcase run."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_scenario_agent(
            ctx["db_url"], name="showcase", scenario="showcase"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test showcase"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=30,
            ), "Showcase execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            assert len(text_parts) >= 1, "Expected at least one text part in DB"

            # Find the final text message (highest msg_seq with text)
            final_text = text_parts[-1][1]
            assert len(final_text) > 20, (
                f"Final text too short, may be truncated: '{final_text}'"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_demo_agent_unchanged(test_database):
    """Non-streaming ACP agent behavior is unchanged by ephemeral changes."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "hello demo"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=30,
            ), "Demo execution did not reach input-required"

            # ACP agents don't emit text_delta, so all messages persist
            events = _get_message_events(ctx["db_url"], session_id)
            assert len(events) >= 1, "Expected at least one message event"

            # Verify text parts exist
            text_parts = _get_text_parts(ctx["db_url"], session_id)
            assert len(text_parts) >= 1, "Expected at least one text part"
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sdk_agent_no_delta_events_in_db(test_database):
    """SDK agent (claude_sdk): DB should contain only complete messages, not deltas.

    The mock-claude-sdk executor emits text_delta streaming events followed by
    complete text messages. Only the complete messages should be persisted.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="sdk-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test sdk streaming"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=45,
            ), "SDK execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            texts = [t for _, t in text_parts]

            # The mock-claude-sdk emits 2 text_delta events and 1 complete message.
            # Only the complete message should be in the DB.
            # Verify no text is a prefix/substring of another (delta+complete overlap)
            for i, t1 in enumerate(texts):
                for j, t2 in enumerate(texts):
                    if i != j and len(t1) > 10 and t1 in t2:
                        pytest.fail(
                            f"Possible delta+complete overlap: text at msg_seq "
                            f"{text_parts[i][0]} is substring of text at "
                            f"msg_seq {text_parts[j][0]}"
                        )

            # Total message events should be reasonable (not inflated by deltas)
            total_events = _all_events_count(ctx["db_url"], session_id)
            assert total_events <= 15, (
                f"Too many events ({total_events}), deltas may be leaking to DB"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sdk_agent_complete_text_preserved(test_database):
    """SDK agent: complete text messages are fully preserved in DB."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="sdk-agent", agent_type="claude_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test sdk complete text"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=45,
            ), "SDK execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            assert len(text_parts) >= 1, "Expected at least one text part in DB"

            # The complete message should contain the full markdown text
            final_text = text_parts[-1][1]
            assert len(final_text) > 20, (
                f"Final text too short, may be truncated: '{final_text[:50]}'"
            )
            # The mock-claude-sdk emits "# Changes Complete\n\nFixed..."
            assert "Changes" in final_text or "Refactoring" in final_text, (
                f"Final text doesn't look like a complete message: '{final_text[:80]}'"
            )
        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_copilot_sdk_agent_no_delta_events_in_db(test_database):
    """Copilot SDK agent: DB should contain only complete messages, not deltas.

    Uses same sdk.rs background_task as Claude — verifies ephemeral fix works
    across both executor types.
    """
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="copilot-agent", agent_type="copilot_sdk"
        )
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test copilot streaming"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            assert _poll_until(
                lambda: _session_status(ctx["db_url"], session_id) == "input-required",
                timeout=45,
            ), "Copilot execution did not reach input-required"

            text_parts = _get_text_parts(ctx["db_url"], session_id)
            texts = [t for _, t in text_parts]

            # Verify no delta+complete overlap
            for i, t1 in enumerate(texts):
                for j, t2 in enumerate(texts):
                    if i != j and len(t1) > 10 and t1 in t2:
                        pytest.fail(
                            f"Possible delta+complete overlap in Copilot: text at "
                            f"msg_seq {text_parts[i][0]} is substring of text at "
                            f"msg_seq {text_parts[j][0]}"
                        )

            # Verify tool_use and thinking parts survived (non-delta content preserved)
            all_events = _get_message_events(ctx["db_url"], session_id)
            has_data_parts = any(
                any(p.get("kind") == "data" for p in payload.get("parts", []))
                for _, payload in all_events
            )
            assert has_data_parts, (
                "Expected data parts (tool_use/thinking) in persisted events"
            )
        finally:
            cleanup_processes([worker])
