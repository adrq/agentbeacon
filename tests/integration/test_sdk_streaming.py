"""Integration tests for streaming text deltas from Claude and Copilot mock SDKs.

These tests spawn the Node.js executor processes with AGENTBEACON_MOCK_SDK=1 and
verify that text_delta events flow through the executor → stdout JSON Lines protocol.

Run with: uv run pytest tests/integration/test_sdk_streaming.py -v
"""

import json
import os
import subprocess
import threading

EXECUTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "executors", "dist")
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")


def _start_executor(script_name):
    """Spawn a Node.js executor process with mock SDK enabled."""
    script = os.path.join(EXECUTORS_DIR, script_name)
    env = os.environ.copy()
    env["AGENTBEACON_MOCK_SDK"] = "1"
    return subprocess.Popen(
        [NODE_PATH, script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _send_command(proc, cmd):
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()


def _collect_events(proc, timeout=30):
    """Read events from stdout in a background thread until 'result' type or timeout."""
    events = []
    got_result = threading.Event()

    def reader():
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") == "result":
                got_result.set()
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    got_result.wait(timeout=timeout)
    return events


def _assert_streaming_events(events):
    """Assert that events contain text_delta messages, complete messages, and a result."""
    delta_messages = [
        e
        for e in events
        if e.get("type") == "message"
        and any(block.get("type") == "text_delta" for block in (e.get("content") or []))
    ]
    assert len(delta_messages) >= 1, (
        f"Expected at least 1 text_delta message, got event types: "
        f"{[e.get('type') for e in events]}"
    )

    complete_messages = [
        e
        for e in events
        if e.get("type") == "message"
        and any(block.get("type") == "text" for block in (e.get("content") or []))
    ]
    assert len(complete_messages) >= 1, "Expected at least 1 complete text message"

    result_events = [e for e in events if e["type"] == "result"]
    assert len(result_events) >= 1, (
        f"Expected result event, got event types: {[e.get('type') for e in events]}"
    )
    assert result_events[-1]["subtype"] == "success"


def test_claude_mock_emits_text_deltas():
    """Claude mock SDK stream_event -> text_delta events reach stdout."""
    proc = _start_executor("claude-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "Fix the tests",
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})
        _assert_streaming_events(events)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_mock_emits_text_deltas():
    """Copilot mock SDK assistant.message_delta events reach stdout."""
    proc = _start_executor("copilot-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "Fix the tests",
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})
        _assert_streaming_events(events)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_mock_emits_thinking_deltas():
    """Claude mock SDK thinking_delta stream events reach stdout."""
    proc = _start_executor("claude-executor.js")
    try:
        _send_command(
            proc, {"type": "start", "prompt": "Fix tests", "cwd": os.getcwd()}
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        thinking_delta_messages = [
            e
            for e in events
            if e.get("type") == "message"
            and any(
                block.get("type") == "thinking_delta"
                for block in (e.get("content") or [])
            )
        ]
        assert len(thinking_delta_messages) >= 1, (
            f"Expected at least 1 thinking_delta message, got types: "
            f"{[(e.get('type'), [b.get('type') for b in (e.get('content') or [])]) for e in events if e.get('type') == 'message']}"
        )

        thinking_messages = [
            e
            for e in events
            if e.get("type") == "message"
            and any(
                block.get("type") == "thinking" for block in (e.get("content") or [])
            )
        ]
        assert len(thinking_messages) >= 1, (
            "Expected at least 1 complete thinking message"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_mock_thinking_before_text():
    """Copilot mock: reasoning events appear before text_delta events in output."""
    proc = _start_executor("copilot-executor.js")
    try:
        _send_command(
            proc, {"type": "start", "prompt": "Fix tests", "cwd": os.getcwd()}
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        messages = [e for e in events if e.get("type") == "message"]
        first_thinking_idx = None
        first_text_delta_idx = None
        for i, msg in enumerate(messages):
            for block in msg.get("content") or []:
                if (
                    block.get("type") in ("thinking", "thinking_delta")
                    and first_thinking_idx is None
                ):
                    first_thinking_idx = i
                if block.get("type") == "text_delta" and first_text_delta_idx is None:
                    first_text_delta_idx = i

        assert first_thinking_idx is not None, "Expected at least 1 thinking message"
        assert first_text_delta_idx is not None, (
            "Expected at least 1 text_delta message"
        )
        assert first_thinking_idx < first_text_delta_idx, (
            f"Thinking (index {first_thinking_idx}) must appear before "
            f"text_delta (index {first_text_delta_idx})"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_mock_emits_reasoning_deltas():
    """Copilot mock reasoning_delta events reach stdout as thinking_delta."""
    proc = _start_executor("copilot-executor.js")
    try:
        _send_command(
            proc, {"type": "start", "prompt": "Fix tests", "cwd": os.getcwd()}
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        thinking_delta_messages = [
            e
            for e in events
            if e.get("type") == "message"
            and any(
                block.get("type") == "thinking_delta"
                for block in (e.get("content") or [])
            )
        ]
        assert len(thinking_delta_messages) >= 1, (
            "Expected at least 1 thinking_delta message from reasoning_delta events"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
