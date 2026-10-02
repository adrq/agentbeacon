# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import subprocess
import threading
import time


MOCK_EXECUTORS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "mock_sdks", "executors"
)
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")


def _start_executor(script_name, extra_env=None):
    script = os.path.join(MOCK_EXECUTORS_DIR, script_name)
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [NODE_PATH, script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _send_commands(proc, commands):
    proc.stdin.write("".join(json.dumps(cmd) + "\n" for cmd in commands))
    proc.stdin.flush()


def _collect_until_result(proc, timeout=30):
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

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    got_result.wait(timeout=timeout)
    assert got_result.is_set(), f"Executor did not emit result within {timeout}s"
    return events


def test_copilot_feed_turn_happy_path():
    proc = _start_executor("copilot-executor.js")
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "First turn"}],
                    "cwd": os.getcwd(),
                }
            ],
        )
        first_events = _collect_until_result(proc)
        first_results = [e for e in first_events if e.get("type") == "result"]
        assert first_results, f"Expected first-turn result, got: {first_events}"
        assert first_results[-1]["subtype"] == "success"

        all_events = []
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
                all_events.append(event)
                if event.get("type") == "result":
                    got_result.set()
                    break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        _send_commands(
            proc,
            [
                {
                    "type": "prompt",
                    "parts": [{"kind": "text", "text": "Second turn message"}],
                }
            ],
        )

        got_result.wait(timeout=30)
        assert got_result.is_set(), (
            f"Executor did not emit second result, events: {all_events}"
        )

        event_types = [e.get("type") for e in all_events]
        assert "accepted" in event_types, (
            f"Expected accepted event in second turn, got: {all_events}"
        )
        accepted_idx = event_types.index("accepted")
        result_idx = event_types.index("result")
        assert accepted_idx < result_idx, (
            f"Expected accepted (idx={accepted_idx}) before result (idx={result_idx}), "
            f"events: {all_events}"
        )

        result_events = [e for e in all_events if e.get("type") == "result"]
        assert result_events, f"Expected result event, got: {all_events}"
        assert result_events[-1]["subtype"] == "success"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_feed_turn_send_failure():
    proc = _start_executor("copilot-executor.js")
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "First turn"}],
                    "cwd": os.getcwd(),
                }
            ],
        )
        first_events = _collect_until_result(proc)
        first_results = [e for e in first_events if e.get("type") == "result"]
        assert first_results, f"Expected first-turn result, got: {first_events}"
        assert first_results[-1]["subtype"] == "success"

        all_events = []
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
                all_events.append(event)
                if event.get("type") == "result":
                    got_result.set()
                    break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        _send_commands(
            proc,
            [
                {
                    "type": "prompt",
                    "parts": [{"kind": "text", "text": "__send_reject__"}],
                }
            ],
        )

        got_result.wait(timeout=30)
        assert got_result.is_set(), (
            f"Executor did not emit result for rejected send, events: {all_events}"
        )

        accepted_events = [e for e in all_events if e.get("type") == "accepted"]
        assert not accepted_events, (
            f"Expected NO accepted event when send() rejects, got: {all_events}"
        )

        result_events = [e for e in all_events if e.get("type") == "result"]
        assert result_events, f"Expected result event, got: {all_events}"
        assert result_events[-1]["subtype"] == "error_during_execution", (
            f"Expected error_during_execution, got: {result_events[-1]}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_feed_turn_emits_accepted():
    proc = _start_executor("claude-executor.js")
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "First turn"}],
                    "cwd": os.getcwd(),
                }
            ],
        )
        first_events = _collect_until_result(proc)
        first_results = [e for e in first_events if e.get("type") == "result"]
        assert first_results, f"Expected first-turn result, got: {first_events}"
        assert first_results[-1]["subtype"] == "success"

        all_events = []
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
                all_events.append(event)
                if event.get("type") == "result":
                    got_result.set()
                    break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        _send_commands(
            proc,
            [
                {
                    "type": "prompt",
                    "parts": [{"kind": "text", "text": "Second turn"}],
                }
            ],
        )

        got_result.wait(timeout=30)
        assert got_result.is_set(), (
            f"Executor did not emit second result, events: {all_events}"
        )

        event_types = [e.get("type") for e in all_events]
        assert "accepted" in event_types, (
            f"Expected accepted event in Claude second turn, got: {all_events}"
        )
        accepted_idx = event_types.index("accepted")
        result_idx = event_types.index("result")
        assert accepted_idx < result_idx, (
            f"Expected accepted (idx={accepted_idx}) before result (idx={result_idx}), "
            f"events: {all_events}"
        )

        result_events = [e for e in all_events if e.get("type") == "result"]
        assert result_events, f"Expected result event, got: {all_events}"
        assert result_events[-1]["subtype"] == "success"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _wait_for_text_delta(events, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for ev in events:
            if ev.get("type") == "message":
                for block in ev.get("content", []):
                    if block.get("type") == "text_delta":
                        return True
        time.sleep(0.02)
    return False


def test_copilot_mid_turn_send_accepted_immediately():
    proc = _start_executor("copilot-executor.js")
    try:
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

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"text": "__slow_turn__"}],
                    "cwd": os.getcwd(),
                }
            ],
        )

        assert _wait_for_text_delta(events), (
            f"Did not see text_delta event, got: {events}"
        )

        _send_commands(
            proc,
            [
                {
                    "type": "prompt",
                    "parts": [{"text": "Steer me"}],
                }
            ],
        )

        got_result.wait(timeout=30)
        assert got_result.is_set(), f"No result event, got: {events}"

        event_types = [e.get("type") for e in events]
        assert "accepted" in event_types, f"Expected accepted event, got: {event_types}"
        accepted_idx = event_types.index("accepted")
        result_idx = len(event_types) - 1 - event_types[::-1].index("result")
        assert accepted_idx < result_idx, (
            f"accepted (idx={accepted_idx}) should be before result (idx={result_idx})"
        )

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1, (
            f"Expected 1 result, got {len(result_events)}: {result_events}"
        )
        assert result_events[0]["subtype"] == "success"

        all_text = ""
        for ev in events:
            if ev.get("type") == "message":
                for block in ev.get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        all_text += block["text"] + " "
        assert "First turn response" in all_text, (
            f"Expected slow turn content, got: {all_text}"
        )
        assert "Acknowledged: Steer me" in all_text, (
            f"Expected mid-turn echo content, got: {all_text}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_mid_turn_send_abort_cancels_all():
    proc = _start_executor("copilot-executor.js")
    try:
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

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"text": "__slow_turn__"}],
                    "cwd": os.getcwd(),
                }
            ],
        )

        assert _wait_for_text_delta(events), (
            f"Did not see text_delta event, got: {events}"
        )

        _send_commands(
            proc,
            [
                {
                    "type": "prompt",
                    "parts": [{"text": "Steer me"}],
                },
                {"type": "cancel"},
            ],
        )

        got_result.wait(timeout=30)
        assert got_result.is_set(), f"No result event, got: {events}"

        result_events = [e for e in events if e.get("type") == "result"]
        assert result_events[0]["subtype"] == "cancelled", (
            f"Expected cancelled, got: {result_events[0]}"
        )

        all_text = ""
        for ev in events:
            if ev.get("type") == "message":
                for block in ev.get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        all_text += block["text"] + " "
        assert "Acknowledged: Steer me" not in all_text, (
            f"Mid-turn prompt should have been discarded on cancel, got: {all_text}"
        )

        _send_commands(proc, [{"type": "stop"}])
        time.sleep(0.5)

        result_events_after = [e for e in events if e.get("type") == "result"]
        assert len(result_events_after) == 1, (
            f"Expected exactly 1 result event total, got {len(result_events_after)}: "
            f"{result_events_after}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


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


def test_stop_resume_emits_accepted():
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
            f"Expected stopped_by_user, got: {stopped_results[-1]}"
        )

        all_events = []
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
                all_events.append(event)
                if event.get("type") == "result":
                    got_result.set()
                    break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        _send_commands(
            proc,
            [{"type": "prompt", "parts": [{"kind": "text", "text": "After stop"}]}],
        )

        got_result.wait(timeout=30)
        assert got_result.is_set(), (
            f"Executor did not emit result after resume, events: {all_events}"
        )

        event_types = [e.get("type") for e in all_events]
        assert "accepted" in event_types, (
            f"Expected accepted event after stop+resume, got: {all_events}"
        )
        accepted_idx = event_types.index("accepted")
        result_idx = event_types.index("result")
        assert accepted_idx < result_idx, (
            f"accepted (idx={accepted_idx}) should precede result (idx={result_idx})"
        )

        result_events = [e for e in all_events if e.get("type") == "result"]
        assert result_events[-1]["subtype"] == "success", (
            f"Expected success after resume, got: {result_events[-1]}"
        )

        message_events = [e for e in all_events if e.get("type") == "message"]
        assert len(message_events) > 0, (
            f"Expected messages after resume, got none; events: {all_events}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
