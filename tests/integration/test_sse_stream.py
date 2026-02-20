"""SSE streaming integration tests.

Validates the per-execution SSE endpoint delivers events in real-time,
supports Last-Event-ID backfill, and closes on terminal state.
"""

import json
import threading
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    cleanup_processes,
    scheduler_context,
    seed_acp_mock_agent,
    start_worker,
)


def _parse_sse_events(lines: list[str]) -> list[dict]:
    """Parse raw SSE lines into list of {id, data} dicts.

    Handles multi-line data: fields per the SSE spec — multiple data: lines
    within a single event are joined with newlines.
    """
    events = []
    current_id = None
    data_parts = []

    for line in lines:
        if line.startswith("id:"):
            current_id = line[3:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].strip())
        elif line == "":
            if data_parts:
                combined = "\n".join(data_parts)
                try:
                    parsed = json.loads(combined)
                    events.append({"id": current_id, "data": parsed})
                except json.JSONDecodeError:
                    events.append({"id": current_id, "data": combined})
                current_id = None
                data_parts = []

    return events


def _stream_sse(url: str, headers: dict = None, timeout: float = 10.0) -> list[str]:
    """Connect to SSE endpoint and collect all lines until stream closes or timeout."""
    lines = []
    try:
        with httpx.stream("GET", url, headers=headers or {}, timeout=timeout) as resp:
            for line in resp.iter_lines():
                lines.append(line)
    except httpx.ReadTimeout:
        pass
    return lines


def _stream_sse_events(
    url: str, headers: dict = None, timeout: float = 10.0
) -> list[dict]:
    """Connect to SSE and return parsed events."""
    lines = _stream_sse(url, headers, timeout)
    return _parse_sse_events(lines)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_delivers_events_for_execution(test_database):
    """SSE endpoint delivers events created during an execution."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for execution to finish
            deadline = time.time() + 30
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status in ("completed", "failed", "input-required"):
                    break
                time.sleep(0.5)
            assert status in ("completed", "failed", "input-required"), (
                f"Execution did not reach terminal state within 30s, stuck at: {status}"
            )

            # Now stream SSE — should get backfill of all events then close
            events = _stream_sse_events(
                f"{ctx['url']}/api/executions/{exec_id}/events/stream",
                timeout=5.0,
            )

            assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"

            # Events should have incrementing IDs
            ids = [int(e["id"]) for e in events]
            assert ids == sorted(ids), "Event IDs should be in order"

            # Each event should have expected fields
            for event in events:
                data = event["data"]
                assert "id" in data
                assert "execution_id" in data
                assert "event_type" in data
                assert "payload" in data

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_backfill_with_last_event_id(test_database):
    """SSE with Last-Event-ID header only returns events after that ID."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for execution to finish
            deadline = time.time() + 30
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status in ("completed", "failed", "input-required"):
                    break
                time.sleep(0.5)
            assert status in ("completed", "failed", "input-required"), (
                f"Execution did not reach terminal state within 30s, stuck at: {status}"
            )

            # Get all events first to know IDs
            all_events = _stream_sse_events(
                f"{ctx['url']}/api/executions/{exec_id}/events/stream",
                timeout=5.0,
            )
            assert len(all_events) >= 3, (
                f"Need at least 3 events for backfill test, got {len(all_events)}"
            )

            # Reconnect with Last-Event-ID set to the 2nd event
            since_id = all_events[1]["id"]
            backfill_events = _stream_sse_events(
                f"{ctx['url']}/api/executions/{exec_id}/events/stream",
                headers={"Last-Event-ID": str(since_id)},
                timeout=5.0,
            )

            # Backfill must return events (not silently empty)
            assert len(backfill_events) > 0, (
                f"Backfill with Last-Event-ID={since_id} returned no events"
            )

            # Backfill IDs should exactly match the tail of all_events after since_id
            expected_tail_ids = [
                int(e["id"]) for e in all_events if int(e["id"]) > int(since_id)
            ]
            backfill_ids = [int(e["id"]) for e in backfill_events]
            assert backfill_ids == expected_tail_ids, (
                f"Backfill IDs should match tail after {since_id}: "
                f"expected {expected_tail_ids}, got {backfill_ids}"
            )

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_terminal_state_closes_stream(test_database):
    """SSE stream closes after execution reaches terminal state (via cancel)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for execution to leave submitted
            deadline = time.time() + 15
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status != "submitted":
                    break
                time.sleep(0.5)

            # Cancel to reach terminal state
            cancel_resp = httpx.post(
                f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=5
            )
            assert cancel_resp.status_code == 200, (
                f"Cancel request failed: {cancel_resp.status_code} {cancel_resp.text}"
            )

            # Wait for canceled status
            deadline = time.time() + 10
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status == "canceled":
                    break
                time.sleep(0.5)
            assert status == "canceled", (
                f"Execution did not reach canceled state, stuck at: {status}"
            )

            # Connect to SSE — stream should close after delivering terminal event
            start = time.time()
            events = _stream_sse_events(
                f"{ctx['url']}/api/executions/{exec_id}/events/stream",
                timeout=10.0,
            )
            elapsed = time.time() - start

            # Stream should close quickly (backfill + terminal detection)
            assert elapsed < 5.0, (
                f"Stream should close on terminal state, took {elapsed:.1f}s"
            )

            # Should include cancel state_change
            state_changes = [
                e for e in events if e["data"]["event_type"] == "state_change"
            ]
            assert len(state_changes) > 0, "Should have at least one state_change"
            cancel_events = [
                sc
                for sc in state_changes
                if sc["data"]["payload"].get("to") == "canceled"
            ]
            assert len(cancel_events) >= 1, "Should have a cancel state_change"

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_cancel_closes_stream(test_database):
    """Canceling an execution causes the SSE stream to close with cancel event."""
    with scheduler_context(db_url=test_database) as ctx:
        # Use a mock agent that pauses — we'll cancel before it completes
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "DELAY_10000"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for execution to become active (working or input-required)
            deadline = time.time() + 15
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status in ("working", "input-required"):
                    break
                time.sleep(0.5)
            assert status in ("working", "input-required"), (
                f"Execution did not become active within 15s, stuck at: {status}"
            )

            # Start SSE in background thread
            sse_events = []
            sse_done = threading.Event()

            def collect_sse():
                evts = _stream_sse_events(
                    f"{ctx['url']}/api/executions/{exec_id}/events/stream",
                    timeout=30.0,
                )
                sse_events.extend(evts)
                sse_done.set()

            t = threading.Thread(target=collect_sse, daemon=True)
            t.start()

            # Give SSE time to connect and backfill
            time.sleep(1)

            # Cancel the execution
            cancel_resp = httpx.post(
                f"{ctx['url']}/api/executions/{exec_id}/cancel", timeout=5
            )
            assert cancel_resp.status_code == 200

            # Wait for SSE stream to close
            assert sse_done.wait(timeout=15), "SSE stream did not close after cancel"

            # Should include cancel state_change
            cancel_events = [
                e
                for e in sse_events
                if e["data"]["event_type"] == "state_change"
                and e["data"]["payload"].get("to") == "canceled"
            ]
            assert len(cancel_events) >= 1, "Should have at least one cancel event"

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sse_nonexistent_execution_returns_404(test_database):
    """SSE endpoint returns 404 for unknown execution ID."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/executions/nonexistent-id/events/stream",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sse_event_format_matches_rest_api(test_database):
    """SSE event data format matches GET /api/executions/{id}/events response."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for execution to finish
            deadline = time.time() + 30
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status in ("completed", "failed", "input-required"):
                    break
                time.sleep(0.5)
            assert status in ("completed", "failed", "input-required"), (
                f"Execution did not reach terminal state within 30s, stuck at: {status}"
            )

            # Get events via REST
            rest_resp = httpx.get(
                f"{ctx['url']}/api/executions/{exec_id}/events", timeout=5
            )
            rest_events = rest_resp.json()

            # Get events via SSE
            sse_events = _stream_sse_events(
                f"{ctx['url']}/api/executions/{exec_id}/events/stream",
                timeout=5.0,
            )

            # Same count
            assert len(sse_events) == len(rest_events), (
                f"SSE delivered {len(sse_events)} events, REST has {len(rest_events)}"
            )

            # Same IDs and event types
            for sse_evt, rest_evt in zip(sse_events, rest_events):
                assert sse_evt["data"]["id"] == rest_evt["id"]
                assert sse_evt["data"]["event_type"] == rest_evt["event_type"]
                assert sse_evt["data"]["execution_id"] == rest_evt["execution_id"]

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sse_no_duplicate_events_on_reconnection(test_database):
    """SSE does not deliver duplicate events across reconnection with Last-Event-ID."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            # Wait for execution to reach terminal state
            deadline = time.time() + 30
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status in ("completed", "failed", "input-required"):
                    break
                time.sleep(0.5)
            assert status in ("completed", "failed", "input-required"), (
                f"Execution did not reach terminal state within 30s, stuck at: {status}"
            )

            stream_url = f"{ctx['url']}/api/executions/{exec_id}/events/stream"

            # First connection: get all events
            first_events = _stream_sse_events(stream_url, timeout=5.0)
            assert len(first_events) >= 3, (
                f"Need at least 3 events for reconnection test, got {len(first_events)}"
            )

            # Pick a mid-point to simulate a client that received some events
            # then disconnected
            midpoint = len(first_events) // 2
            last_seen_id = first_events[midpoint]["id"]

            # Second connection: reconnect with Last-Event-ID
            second_events = _stream_sse_events(
                stream_url,
                headers={"Last-Event-ID": str(last_seen_id)},
                timeout=5.0,
            )

            # Reconnection must return events (not silently empty)
            assert len(second_events) > 0, (
                f"Reconnection with Last-Event-ID={last_seen_id} returned no events"
            )

            # All IDs from second stream must be strictly after last_seen_id
            second_ids = [int(e["id"]) for e in second_events]
            assert all(eid > int(last_seen_id) for eid in second_ids), (
                f"Second stream should only have IDs > {last_seen_id}, got {second_ids}"
            )

            # No overlap between first-segment IDs (up to midpoint) and second-segment
            first_segment_ids = {int(e["id"]) for e in first_events[: midpoint + 1]}
            overlap = first_segment_ids & set(second_ids)
            assert not overlap, f"Duplicate IDs across reconnection: {overlap}"

            # First segment + reconnection should cover all events
            all_ids = {int(e["id"]) for e in first_events}
            assert first_segment_ids | set(second_ids) == all_ids, (
                f"First segment {first_segment_ids} + reconnection {set(second_ids)} "
                f"should cover all IDs {all_ids}"
            )

        finally:
            cleanup_processes([worker])
