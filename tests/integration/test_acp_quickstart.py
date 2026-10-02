# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import pytest

from tests.testhelpers import (
    cleanup_processes,
)
from tests.worker_test_helpers import (
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


def test_quickstart_basic_acp_task(mock_scheduler):
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="Write a hello world script")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete successfully: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_quickstart_session_updates_in_history(mock_scheduler):
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="STREAM_CHUNKS")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is None, (
            f"Task should complete successfully: {results[0]}"
        )
        output = get_agent_output(url)
        assert output is not None, (
            "Output should contain accumulated agent messages from events"
        )
        parts = output.get("parts", []) if isinstance(output, dict) else []
        assert len(parts) >= 2, (
            f"Output should contain multiple parts from session/update notifications: {output}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_quickstart_subprocess_crash_handling(mock_scheduler):
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="EXIT_1")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is not None, (
            f"Task should fail when subprocess crashes: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])


def test_quickstart_malformed_jsonrpc(mock_scheduler):
    url, _, _ = mock_scheduler
    clear_state(url)

    enqueue_session(url, prompt_text="INVALID_JSONRPC")
    worker = start_worker(url)
    try:
        assert poll_until(lambda: len(get_results(url)) > 0, timeout=30), (
            "Worker did not report session result"
        )
        results = get_results(url)
        assert len(results) == 1
        assert results[0]["error"] is not None, (
            f"Task should fail on malformed JSON-RPC: {results[0]}"
        )
    finally:
        mark_complete(url)
        time.sleep(1)
        cleanup_processes([worker])
