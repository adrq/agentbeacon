# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import threading

from tests.integration.test_sdk_stop_turn import (
    _collect_until_result,
    _send_commands,
    _start_executor,
)


def _wait_for_stderr(proc, marker, timeout=10):
    found = threading.Event()

    def drain():
        for line in proc.stderr:
            if marker in line:
                found.set()

    threading.Thread(target=drain, daemon=True).start()
    assert found.wait(timeout=timeout), (
        f"Did not see {marker!r} in executor stderr within {timeout}s"
    )


def test_stop_then_resume_responds():
    proc = _start_executor("claude-executor.js")
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "Do work"}],
                    "cwd": os.getcwd(),
                }
            ],
        )
        _wait_for_stderr(proc, "[mock-claude-sdk] query() called")

        _send_commands(proc, [{"type": "stop_turn"}])

        stopped_events = _collect_until_result(proc)
        stopped_results = [e for e in stopped_events if e.get("type") == "result"]
        assert stopped_results, f"Expected stop result, got: {stopped_events}"
        assert stopped_results[-1]["subtype"] == "stopped_by_user", (
            f"AbortError detection failed: expected stopped_by_user, "
            f"got: {stopped_results[-1]}"
        )

        _send_commands(
            proc,
            [{"type": "prompt", "parts": [{"kind": "text", "text": "After stop"}]}],
        )

        resumed_events = _collect_until_result(proc)
        resumed_results = [e for e in resumed_events if e.get("type") == "result"]
        assert resumed_results, f"Expected resumed result, got: {resumed_events}"
        assert resumed_results[-1]["subtype"] == "success", (
            f"Expected resumed session to succeed, got: {resumed_results[-1]}"
        )

        resumed_messages = [e for e in resumed_events if e.get("type") == "message"]
        assert len(resumed_messages) > 0, (
            f"Expected messages after resume, got none; events: {resumed_events}"
        )

        resumed_text = " ".join(
            block.get("text", "")
            for event in resumed_events
            if event.get("type") == "message" and event.get("role") == "assistant"
            for block in event.get("content", [])
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        assert "configuration file" in resumed_text, (
            f"Expected showcaseTurn output after resume, got: {resumed_text!r}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
