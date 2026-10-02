# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import pytest

from tests.testhelpers import cleanup_processes

from tests.worker_test_helpers import (
    create_mock_scheduler,
    start_worker,
    clear_state,
    enqueue_session,
    enqueue_prompt,
    mark_complete,
    get_sync_log,
    get_events,
    get_results,
    poll_until,
)


@pytest.fixture()
def mock_scheduler():
    scheduler_url, port, proc, pm = create_mock_scheduler()

    yield scheduler_url, port, proc

    cleanup_processes([proc])
    pm.release_port(port)


def test_long_poll_active_during_agent_turn(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    clear_state(scheduler_url)

    enqueue_session(scheduler_url, prompt_text="DELAY_5")

    worker = start_worker(scheduler_url)
    try:
        assert poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "running"
                for e in get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), "Worker did not start active-turn long-poll during turn"

        assert poll_until(lambda: len(get_results(scheduler_url)) > 0, timeout=30)

        sync_log = get_sync_log(scheduler_url)
        first_running = next(
            (
                i
                for i, e in enumerate(sync_log)
                if e.get("sessionState", {}).get("status") == "running"
            ),
            None,
        )
        first_result = next(
            (i for i, e in enumerate(sync_log) if e.get("sessionResult")),
            None,
        )
        assert first_running is not None and first_result is not None, (
            f"Missing expected entries: running={first_running}, result={first_result}"
        )
        assert first_running < first_result, (
            f"running (idx {first_running}) should precede "
            f"first result (idx {first_result})"
        )
    finally:
        mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_prompt_queued_during_turn_delivered_after(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    clear_state(scheduler_url)

    enqueue_session(scheduler_url, prompt_text="DELAY_3")

    worker = start_worker(scheduler_url)
    try:
        assert poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "waiting_for_event"
                for e in get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), "Worker did not start long-poll during turn"

        enqueue_prompt(scheduler_url, prompt_text="mid-turn follow-up")

        assert poll_until(lambda: len(get_results(scheduler_url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(get_results(scheduler_url))}: "
            f"{get_results(scheduler_url)}"
        )

        results = get_results(scheduler_url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-1"
        assert results[1]["sessionId"] == "sess-1"
        events = get_events(scheduler_url)
        event_text = str([e.get("payload") for e in events])
        assert "mid-turn follow-up" in event_text, (
            f"Events should contain echoed follow-up prompt: {event_text}"
        )
    finally:
        mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_multiple_prompts_queued_during_turn(mock_scheduler):
    scheduler_url, _, _ = mock_scheduler
    clear_state(scheduler_url)

    enqueue_session(scheduler_url, prompt_text="DELAY_5")

    worker = start_worker(scheduler_url)
    try:
        assert poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "waiting_for_event"
                for e in get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), "Worker did not start long-poll during turn"

        enqueue_prompt(scheduler_url, prompt_text="first mid-turn msg")
        time.sleep(0.2)
        enqueue_prompt(scheduler_url, prompt_text="second mid-turn msg")

        assert poll_until(lambda: len(get_results(scheduler_url)) >= 3, timeout=45), (
            f"Expected 3 results, got {len(get_results(scheduler_url))}: "
            f"{get_results(scheduler_url)}"
        )

        results = get_results(scheduler_url)
        assert len(results) == 3
        for r in results:
            assert r["sessionId"] == "sess-1"
        events = get_events(scheduler_url)
        event_payloads = [str(e.get("payload")) for e in events]
        first_idx = next(
            (i for i, p in enumerate(event_payloads) if "first mid-turn msg" in p),
            None,
        )
        second_idx = next(
            (i for i, p in enumerate(event_payloads) if "second mid-turn msg" in p),
            None,
        )
        assert first_idx is not None, (
            f"Events should contain first follow-up echo: {event_payloads}"
        )
        assert second_idx is not None, (
            f"Events should contain second follow-up echo: {event_payloads}"
        )
        assert first_idx < second_idx, (
            f"First follow-up (idx {first_idx}) should precede "
            f"second (idx {second_idx}) in event sequence"
        )
    finally:
        mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])
