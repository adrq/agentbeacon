# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import subprocess
import threading

MOCK_EXECUTORS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "mock_sdks", "executors"
)
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")


def _start_executor(script_name):
    script = os.path.join(MOCK_EXECUTORS_DIR, script_name)
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
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
    events = []
    got_terminal = threading.Event()

    def reader():
        while True:
            line = proc.stdout.readline()
            if not line:
                got_terminal.set()
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") in ("result", "error"):
                got_terminal.set()
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    got_terminal.wait(timeout=timeout)
    return events


def _assert_streaming_events(events):
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
    proc = _start_executor("claude-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Fix the tests"}],
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
    proc = _start_executor("copilot-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Fix the tests"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})
        _assert_streaming_events(events)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_executor_buffering_exact_assertions():
    proc = _start_executor("claude-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Test buffering"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        messages = [e for e in events if e.get("type") == "message"]
        assert len(messages) >= 1, "Expected at least 1 message event"

        delta_messages = [
            e
            for e in messages
            if any(
                block.get("type") in ("text_delta", "thinking_delta")
                for block in (e.get("content") or [])
            )
        ]
        assert len(delta_messages) >= 3, (
            f"Expected >= 3 delta messages (thinking + text), got {len(delta_messages)}"
        )

        final_assistant_msgs = [
            e
            for e in messages
            if e.get("role") == "assistant"
            and any(
                block.get("type") in ("text", "thinking", "tool_use")
                for block in (e.get("content") or [])
            )
        ]
        assert len(final_assistant_msgs) == 5, (
            f"Expected exactly 5 final assistant messages (one per API call), "
            f"got {len(final_assistant_msgs)}. Block types per msg: "
            f"{[[b.get('type') for b in (m.get('content') or [])] for m in final_assistant_msgs]}"
        )

        assistant_texts = []
        for msg in final_assistant_msgs:
            for block in msg.get("content") or []:
                if block.get("type") == "text":
                    assistant_texts.append(block.get("text", ""))
        for i, t1 in enumerate(assistant_texts):
            for j, t2 in enumerate(assistant_texts):
                if i != j and len(t1) > 10 and t1 in t2:
                    assert False, (
                        f"Intermediate snapshot leaked: '{t1[:60]}' is a prefix of "
                        f"'{t2[:60]}' — buffering failed"
                    )

        assert events[-1].get("type") == "result", (
            f"Expected result event last, got type={events[-1].get('type')}"
        )
        assert events[-1].get("subtype") == "success", (
            f"Expected success result, got subtype={events[-1].get('subtype')}"
        )

        assert any(
            "I'll start by reading the configuration file" in t for t in assistant_texts
        ), f"API call 1 text missing: {assistant_texts}"
        assert any(
            "Now searching for TODO/FIXME items" in t for t in assistant_texts
        ), f"API call 2 text missing: {assistant_texts}"
        assert any(
            "Changes Complete" in t and "Fixed both issues" in t
            for t in assistant_texts
        ), f"Final summary text missing: {assistant_texts}"

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_mock_emits_thinking_deltas():
    proc = _start_executor("claude-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Fix tests"}],
                "cwd": os.getcwd(),
            },
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
    proc = _start_executor("copilot-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Fix tests"}],
                "cwd": os.getcwd(),
            },
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
            f"expected thinking (index {first_thinking_idx}) before "
            f"text_delta (index {first_text_delta_idx})"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_mid_stream_failure_discards_buffer():
    proc = _start_executor("claude-executor.js")
    proc.terminate()
    proc.wait(timeout=5)

    script = os.path.join(MOCK_EXECUTORS_DIR, "claude-executor.js")
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
    env["AGENTBEACON_MOCK_SDK_MID_STREAM_FAILURE"] = "1"
    proc = subprocess.Popen(
        [NODE_PATH, script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Trigger mid-stream failure"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        assistant_messages = [
            e
            for e in events
            if e.get("type") == "message"
            and e.get("role") == "assistant"
            and any(block.get("type") == "text" for block in (e.get("content") or []))
        ]
        stale_messages = [
            e
            for e in assistant_messages
            if any(
                "Stale content" in (block.get("text") or "")
                for block in (e.get("content") or [])
            )
        ]
        assert len(stale_messages) == 0, (
            f"Stale assistant content leaked through error path: {stale_messages}"
        )

        terminal_events = [e for e in events if e.get("type") in ("result", "error")]
        assert len(terminal_events) >= 1, (
            f"Expected a terminal event (result/error) after mid-stream failure, "
            f"got event types: {[e.get('type') for e in events]}"
        )
        last_terminal = terminal_events[-1]
        if last_terminal.get("type") == "result":
            assert last_terminal.get("subtype") != "success", (
                "Expected non-success result after mid-stream failure"
            )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_mock_emits_reasoning_deltas():
    proc = _start_executor("copilot-executor.js")
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "Fix tests"}],
                "cwd": os.getcwd(),
            },
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
