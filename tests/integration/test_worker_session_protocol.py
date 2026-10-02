# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import pytest
import requests

from tests.testhelpers import cleanup_processes

from tests.worker_test_helpers import (
    create_mock_scheduler,
    start_worker as _start_worker,
    clear_state as _clear_state,
    enqueue_session as _enqueue_session_full,
    enqueue_prompt as _enqueue_prompt,
    mark_complete as _mark_complete,
    get_sync_log as _get_sync_log,
    get_results as _get_results,
    poll_until as _poll_until,
)


def _enqueue_session(scheduler_url, session_id="sess-1", execution_id="exec-1"):
    _enqueue_session_full(scheduler_url, session_id, execution_id, "hello from test")


@pytest.fixture()
def mock_scheduler():
    scheduler_url, port, proc, pm = create_mock_scheduler()

    yield scheduler_url, port, proc

    cleanup_processes([proc])
    pm.release_port(port)


def test_worker_idle_gets_no_action(mock_scheduler):
    scheduler_url, _, scheduler_proc = mock_scheduler
    _clear_state(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        time.sleep(3)

        assert worker.poll() is None, "Worker should still be running"

        sync_log = _get_sync_log(scheduler_url)
        assert len(sync_log) >= 2, f"Expected multiple idle syncs, got {len(sync_log)}"

        for entry in sync_log:
            assert entry == {}, f"Idle sync should be empty: {entry}"
    finally:
        cleanup_processes([worker])


def test_worker_receives_session_assignment(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 1
        assert results[0]["sessionId"] == "sess-1"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_reports_agent_session_id(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30), (
            "Worker did not report session result"
        )

        results = _get_results(scheduler_url)
        assert results[0]["agentSessionId"] is not None, (
            f"Expected non-null agentSessionId, got {results[0]}"
        )
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_enters_waiting_after_result(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30)

        assert _poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "waiting_for_event"
                for e in _get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), f"Expected waiting_for_event syncs, got: {_get_sync_log(scheduler_url)}"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_receives_prompt_delivery(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)
    _enqueue_prompt(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(_get_results(scheduler_url))}: {_get_results(scheduler_url)}"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-1"
        assert results[1]["sessionId"] == "sess-1"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_receives_a2a_prompt_delivery(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)

    resp = requests.post(
        f"{scheduler_url}/test/enqueue_prompt",
        json={
            "sessionId": "sess-1",
            "executionId": "exec-1",
            "taskPayload": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "follow-up as A2A message"}],
                }
            },
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"Enqueue A2A prompt failed: {resp.text}"

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(_get_results(scheduler_url))}: {_get_results(scheduler_url)}"
        )

        results = _get_results(scheduler_url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-1"
        assert results[1]["sessionId"] == "sess-1"
    finally:
        _mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_worker_handles_session_complete(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    _enqueue_session(scheduler_url)
    _mark_complete(scheduler_url)

    worker = _start_worker(scheduler_url)
    try:
        assert _poll_until(lambda: len(_get_results(scheduler_url)) > 0, timeout=30)

        assert worker.poll() is None, (
            "Worker should still be running after session_complete"
        )

        def has_idle_after_result():
            sync_log = _get_sync_log(scheduler_url)
            found_result = False
            for entry in sync_log:
                if "sessionResult" in entry and entry["sessionResult"]:
                    found_result = True
                elif found_result and entry == {}:
                    return True
            return False

        assert _poll_until(has_idle_after_result, timeout=10), (
            f"Expected idle sync after session result, sync_log: {_get_sync_log(scheduler_url)}"
        )
    finally:
        cleanup_processes([worker])


def test_worker_invalid_agent_graceful(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    _clear_state(scheduler_url)

    bad_payload = {
        "agent_id": "bad-agent",
        "driver": {"platform": "acp", "config": {}},
        "agent_config": {
            "command": "/nonexistent/path/to/agent",
            "args": [],
            "timeout": 5,
        },
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
    }
    requests.post(
        f"{scheduler_url}/test/enqueue_session",
        json={
            "sessionId": "sess-bad",
            "executionId": "exec-bad",
            "taskPayload": bad_payload,
        },
        timeout=5,
    )

    worker = _start_worker(scheduler_url)
    try:
        time.sleep(5)

        assert worker.poll() is None, "Worker should not crash on bad agent config"
    finally:
        cleanup_processes([worker])
