"""Worker mid-turn prompt delivery tests.

Validates the event-driven worker architecture: the worker long-polls the
scheduler concurrently while the agent is processing, enabling mid-turn
message delivery.

All tests use the ACP mock agent with DELAY_N special commands for timing.
"""

import time

import pytest

from tests.testhelpers import cleanup_processes

from tests.integration.worker_test_helpers import (
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
    """Start mock scheduler and yield (url, port, process)."""
    scheduler_url, port, proc, pm = create_mock_scheduler()

    yield scheduler_url, port, proc

    cleanup_processes([proc])
    pm.release_port(port)


def test_long_poll_active_during_agent_turn(mock_scheduler):
    """Worker long-polls scheduler during agent turn (not just after)."""
    scheduler_url, _, _ = mock_scheduler
    clear_state(scheduler_url)

    # DELAY_5: agent takes 5 seconds to respond, giving ample time to
    # observe the waiting_for_event sync before the result arrives.
    enqueue_session(scheduler_url, prompt_text="DELAY_5")

    worker = start_worker(scheduler_url)
    try:
        # Wait for a waiting_for_event sync to appear in the log.
        # With DELAY_5, the agent takes 5 seconds, so this should appear
        # well before the result is reported.
        assert poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "waiting_for_event"
                for e in get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), "Worker did not start long-poll during turn"

        # Now wait for the turn to complete normally
        assert poll_until(lambda: len(get_results(scheduler_url)) > 0, timeout=30)

        # Verify ordering: waiting_for_event appeared before the first result
        # in the sync log (proves long-poll was active during the turn).
        sync_log = get_sync_log(scheduler_url)
        first_waiting = next(
            (
                i
                for i, e in enumerate(sync_log)
                if e.get("sessionState", {}).get("status") == "waiting_for_event"
            ),
            None,
        )
        first_result = next(
            (i for i, e in enumerate(sync_log) if e.get("sessionResult")),
            None,
        )
        assert first_waiting is not None and first_result is not None, (
            f"Missing expected entries: waiting={first_waiting}, result={first_result}"
        )
        assert first_waiting < first_result, (
            f"waiting_for_event (idx {first_waiting}) should precede "
            f"first result (idx {first_result})"
        )
    finally:
        mark_complete(scheduler_url)
        time.sleep(1)
        cleanup_processes([worker])


def test_prompt_queued_during_turn_delivered_after(mock_scheduler):
    """Prompt queued during agent turn is delivered after turn completes."""
    scheduler_url, _, _ = mock_scheduler
    clear_state(scheduler_url)

    # DELAY_3: agent takes 3 seconds, giving time to enqueue mid-turn
    enqueue_session(scheduler_url, prompt_text="DELAY_3")

    worker = start_worker(scheduler_url)
    try:
        # Wait for long-poll to start (proves agent is running + worker is listening)
        assert poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "waiting_for_event"
                for e in get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), "Worker did not start long-poll during turn"

        # Enqueue follow-up prompt WHILE agent is processing DELAY_3
        enqueue_prompt(scheduler_url, prompt_text="mid-turn follow-up")

        # Wait for 2 results: initial DELAY_3 + follow-up echo
        assert poll_until(lambda: len(get_results(scheduler_url)) >= 2, timeout=30), (
            f"Expected 2 results, got {len(get_results(scheduler_url))}: "
            f"{get_results(scheduler_url)}"
        )

        results = get_results(scheduler_url)
        assert len(results) == 2
        assert results[0]["sessionId"] == "sess-1"
        assert results[1]["sessionId"] == "sess-1"
        # Verify the follow-up was processed (output arrives via mid-turn events)
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
    """Multiple prompts queued during turn are delivered sequentially."""
    scheduler_url, _, _ = mock_scheduler
    clear_state(scheduler_url)

    # DELAY_5: agent takes 5 seconds, time to enqueue multiple prompts
    enqueue_session(scheduler_url, prompt_text="DELAY_5")

    worker = start_worker(scheduler_url)
    try:
        # Wait for long-poll to confirm agent is running
        assert poll_until(
            lambda: any(
                e.get("sessionState", {}).get("status") == "waiting_for_event"
                for e in get_sync_log(scheduler_url)
            ),
            timeout=10,
        ), "Worker did not start long-poll during turn"

        # Enqueue 2 follow-up prompts with distinct text during the turn
        enqueue_prompt(scheduler_url, prompt_text="first mid-turn msg")
        time.sleep(0.2)  # ensure ordering
        enqueue_prompt(scheduler_url, prompt_text="second mid-turn msg")

        # Wait for 3 results: initial DELAY_5 + 2 follow-ups
        assert poll_until(lambda: len(get_results(scheduler_url)) >= 3, timeout=45), (
            f"Expected 3 results, got {len(get_results(scheduler_url))}: "
            f"{get_results(scheduler_url)}"
        )

        results = get_results(scheduler_url)
        assert len(results) == 3
        for r in results:
            assert r["sessionId"] == "sess-1"
        # Verify follow-ups were processed in order (output via mid-turn events)
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
