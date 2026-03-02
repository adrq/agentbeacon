"""Integration tests for ACP session/update variants.

Tests that worker correctly handles all SessionUpdate variants from the ACP spec,
especially those without a content field (plan, tool_call, tool_call_update,
available_commands_update, current_mode_update).

These variants were causing deserialization failures before the enum fix.
"""

import time

import pytest

from tests.testhelpers import cleanup_processes
from tests.integration.worker_test_helpers import (
    create_mock_scheduler,
    start_worker,
    clear_state,
    enqueue_session,
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


def test_session_update_plan_variant(mock_scheduler):
    """Test worker handles session/update with plan variant (no content field)."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_PLAN")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_update_tool_call_variant(mock_scheduler):
    """Test worker handles session/update with tool_call variant (minimal content)."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_TOOL_CALL")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_update_mode_update_variant(mock_scheduler):
    """Test worker handles session/update with current_mode_update variant."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_MODE_UPDATE")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_update_commands_update_variant(mock_scheduler):
    """Test worker handles session/update with available_commands_update variant."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_COMMANDS_UPDATE")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_update_tool_group_variant(mock_scheduler):
    """Test worker handles SEND_TOOL_GROUP (tool_call + tool_call_update pair)."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_TOOL_GROUP")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_update_tool_stream_variant(mock_scheduler):
    """Test worker handles SEND_TOOL_STREAM (6 tool_call + 6 tool_call_update pairs)."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_TOOL_STREAM")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_session_update_streaming_markdown_variant(mock_scheduler):
    """Test worker handles SEND_STREAMING_MARKDOWN (multiple agent_message_chunks with delays)."""
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="SEND_STREAMING_MARKDOWN")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete (no deserialization error): {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
