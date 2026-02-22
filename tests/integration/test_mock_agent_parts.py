"""Integration tests for Part::Data normalization in worker output.

Verifies that the worker correctly converts ACP session/update variants
into Part types. Chunk variants (messages, thoughts) unwrap the ContentBlock
wrapper. All other variants pass through as raw JSON with sessionUpdate
renamed to type:
- SEND_MARKDOWN → Part::Text with markdown content
- SEND_TOOL_CALL → Part::Data with data.type: "tool_call"
- SEND_PLAN → Part::Data with data.type: "plan"
- SEND_MODE_UPDATE → Part::Data with data.type: "current_mode_update"
- SEND_COMMANDS_UPDATE → Part::Data with data.type: "available_commands_update"
- SEND_THOUGHT → Part::Data with data.type: "agent_thought_chunk"
- SEND_TOOL_CALL_UPDATE → Part::Data with data.type: "tool_call_update"
"""

import time

import pytest

from tests.testhelpers import cleanup_processes
from tests.integration.worker_test_helpers import (
    create_mock_scheduler,
    start_worker,
    clear_state,
    enqueue_session,
    get_agent_output,
    get_results,
    mark_complete,
    poll_until,
)


@pytest.fixture()
def mock_scheduler():
    url, port, proc, pm = create_mock_scheduler()
    yield url, port, proc
    cleanup_processes([proc])
    pm.release_port(port)


def _get_output_parts(url):
    """Extract agent output parts from events or sync results."""
    results = get_results(url)
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0]["error"] is None, f"Unexpected error: {results[0]}"
    output = get_agent_output(url)
    assert output is not None, "Expected non-null output from events or sync"
    assert output["role"] == "agent"
    return output["parts"]


def test_send_markdown_produces_text_part(mock_scheduler):
    """SEND_MARKDOWN → Part::Text with rich markdown content."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_MARKDOWN")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        text_parts = [p for p in parts if p.get("kind") == "text"]
        assert len(text_parts) >= 1, f"Expected at least one text part, got: {parts}"

        # The markdown should contain headers, tables, code blocks
        combined_text = " ".join(p["text"] for p in text_parts)
        assert "# Analysis Report" in combined_text
        assert "```python" in combined_text
        assert "|" in combined_text  # table
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_send_tool_call_produces_data_part(mock_scheduler):
    """SEND_TOOL_CALL → Part::Data with data.type: "tool_call"."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_TOOL_CALL")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        data_parts = [p for p in parts if p.get("kind") == "data"]
        tool_call_parts = [
            p for p in data_parts if p["data"].get("type") == "tool_call"
        ]
        assert len(tool_call_parts) >= 1, (
            f"Expected at least one Part::Data with type 'tool_call', got: {parts}"
        )

        tc = tool_call_parts[0]["data"]
        assert "toolCallId" in tc, f"tool_call should have toolCallId: {tc}"
        assert "title" in tc, f"tool_call should have title: {tc}"
        assert "content" in tc, f"tool_call should include content: {tc}"
        assert isinstance(tc["content"], list), f"content should be an array: {tc}"
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_send_plan_produces_data_part(mock_scheduler):
    """SEND_PLAN → Part::Data with data.type: "plan"."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_PLAN")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        data_parts = [p for p in parts if p.get("kind") == "data"]
        plan_parts = [p for p in data_parts if p["data"].get("type") == "plan"]
        assert len(plan_parts) >= 1, (
            f"Expected at least one Part::Data with type 'plan', got: {parts}"
        )

        plan = plan_parts[0]["data"]
        assert "entries" in plan, f"plan should have entries: {plan}"
        assert isinstance(plan["entries"], list), f"entries should be an array: {plan}"
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_send_mode_update_produces_data_part(mock_scheduler):
    """SEND_MODE_UPDATE → Part::Data with data.type: "current_mode_update"."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_MODE_UPDATE")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        data_parts = [p for p in parts if p.get("kind") == "data"]
        mode_parts = [
            p for p in data_parts if p["data"].get("type") == "current_mode_update"
        ]
        assert len(mode_parts) >= 1, (
            f"Expected at least one Part::Data with type 'current_mode_update', got: {parts}"
        )

        mode = mode_parts[0]["data"]
        assert "currentModeId" in mode, (
            f"current_mode_update should have currentModeId: {mode}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_send_commands_update_produces_data_part(mock_scheduler):
    """SEND_COMMANDS_UPDATE → Part::Data with data.type: "available_commands_update"."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_COMMANDS_UPDATE")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        data_parts = [p for p in parts if p.get("kind") == "data"]
        cmd_parts = [
            p
            for p in data_parts
            if p["data"].get("type") == "available_commands_update"
        ]
        assert len(cmd_parts) >= 1, (
            f"Expected at least one Part::Data with type 'available_commands_update', got: {parts}"
        )

        cmds = cmd_parts[0]["data"]
        assert "availableCommands" in cmds, (
            f"available_commands_update should have availableCommands: {cmds}"
        )
        assert isinstance(cmds["availableCommands"], list), (
            f"availableCommands should be an array: {cmds}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_send_thought_produces_data_part(mock_scheduler):
    """SEND_THOUGHT → Part::Data with data.type: "agent_thought_chunk"."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_THOUGHT")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        data_parts = [p for p in parts if p.get("kind") == "data"]
        thinking_parts = [
            p for p in data_parts if p["data"].get("type") == "agent_thought_chunk"
        ]
        assert len(thinking_parts) >= 1, (
            f"Expected at least one Part::Data with type 'agent_thought_chunk', got: {parts}"
        )

        thought = thinking_parts[0]["data"]
        assert "text" in thought, f"thinking should have text: {thought}"
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_send_tool_call_update_produces_data_part(mock_scheduler):
    """SEND_TOOL_CALL_UPDATE → Part::Data with data.type: "tool_call_update"."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_TOOL_CALL_UPDATE")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30)
        parts = _get_output_parts(url)

        data_parts = [p for p in parts if p.get("kind") == "data"]
        update_parts = [
            p for p in data_parts if p["data"].get("type") == "tool_call_update"
        ]
        assert len(update_parts) >= 1, (
            f"Expected at least one Part::Data with type 'tool_call_update', got: {parts}"
        )

        tc_update = update_parts[0]["data"]
        assert "toolCallId" in tc_update, (
            f"tool_call_update should have toolCallId: {tc_update}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
