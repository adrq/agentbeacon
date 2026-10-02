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


def _start_executor():
    script = os.path.join(MOCK_EXECUTORS_DIR, "copilot-executor.js")
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
    assert got_result.is_set(), (
        f"Executor did not emit a terminal 'result' event within {timeout}s "
        f"(got {len(events)} events: {[e.get('type') for e in events]})"
    )
    return events


def _extract_content_blocks(events):
    blocks = []
    for e in events:
        if e.get("type") == "message":
            for block in e.get("content", []):
                blocks.append(block)
    return blocks


def _run_showcase_scenario():
    proc = _start_executor()
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
        return events, _extract_content_blocks(events)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_emits_tool_use_blocks():
    events, blocks = _run_showcase_scenario()

    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    assert len(tool_use_blocks) >= 2, (
        f"Expected at least 2 tool_use blocks (Bash + Read), got {len(tool_use_blocks)}: "
        f"{[b.get('type') for b in blocks]}"
    )

    for block in tool_use_blocks:
        assert "id" in block, f"tool_use block missing 'id': {block}"
        assert "name" in block, f"tool_use block missing 'name': {block}"


def test_copilot_emits_tool_result_blocks():
    events, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) >= 2, (
        f"Expected at least 2 tool_result blocks, got {len(tool_result_blocks)}: "
        f"{[b.get('type') for b in blocks]}"
    )

    for block in tool_result_blocks:
        assert "tool_use_id" in block, (
            f"tool_result block missing 'tool_use_id': {block}"
        )


def test_copilot_tool_result_has_correct_tool_use_id():
    _, blocks = _run_showcase_scenario()

    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]

    tool_use_ids = {b["id"] for b in tool_use_blocks}
    tool_result_refs = {b["tool_use_id"] for b in tool_result_blocks}

    assert tool_result_refs.issubset(tool_use_ids), (
        f"tool_result references {tool_result_refs} but tool_use IDs are {tool_use_ids}"
    )
    assert tool_use_ids == tool_result_refs, (
        f"Mismatch: tool_use IDs {tool_use_ids} != tool_result refs {tool_result_refs}"
    )


def test_copilot_tool_use_includes_input():
    _, blocks = _run_showcase_scenario()

    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    assert len(tool_use_blocks) >= 1, "Expected at least 1 tool_use block"

    blocks_with_input = [b for b in tool_use_blocks if b.get("input") is not None]
    assert len(blocks_with_input) >= 2, (
        f"Expected at least 2 tool_use blocks with input, got {len(blocks_with_input)}: "
        f"{tool_use_blocks}"
    )

    bash_blocks = [b for b in tool_use_blocks if b.get("name") == "Bash"]
    assert len(bash_blocks) >= 1, (
        f"Expected Bash tool_use block, got: {tool_use_blocks}"
    )
    assert "command" in bash_blocks[0]["input"], (
        f"Bash tool_use input should have 'command': {bash_blocks[0]}"
    )


def test_copilot_tool_result_includes_content():
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) >= 1, "Expected at least 1 tool_result block"

    blocks_with_content = [
        b for b in tool_result_blocks if b.get("content") not in (None, "")
    ]
    assert len(blocks_with_content) >= 2, (
        f"Expected at least 2 tool_result blocks with content, got {len(blocks_with_content)}: "
        f"{tool_result_blocks}"
    )


def test_copilot_successful_tool_not_marked_error():
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) >= 1, "Expected at least 1 tool_result block"

    successful_results = [
        b for b in tool_result_blocks if b["tool_use_id"] in ("call_001", "call_002")
    ]
    assert len(successful_results) >= 2, (
        f"Expected at least 2 successful tool_result blocks, got: {tool_result_blocks}"
    )
    for block in successful_results:
        assert block.get("is_error") is False, (
            f"Expected is_error=false for successful tool, got: {block}"
        )


def test_copilot_failed_tool_marked_error():
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    failed_results = [b for b in tool_result_blocks if b["tool_use_id"] == "call_003"]
    assert len(failed_results) == 1, (
        f"Expected 1 failed tool_result (call_003), got: {tool_result_blocks}"
    )
    assert failed_results[0]["is_error"] is True, (
        f"Expected is_error=true for failed tool, got: {failed_results[0]}"
    )


def test_copilot_failed_tool_captures_error_message():
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    failed_blocks = [b for b in tool_result_blocks if b["tool_use_id"] == "call_003"]
    assert len(failed_blocks) == 1, (
        f"Expected 1 tool_result for call_003, got: {tool_result_blocks}"
    )
    assert failed_blocks[0]["content"] == "Permission denied: /etc/readonly-file", (
        f"Expected error message in content, got: {failed_blocks[0]}"
    )


def test_copilot_structured_contents_preserved():
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    bash_results = [b for b in tool_result_blocks if b["tool_use_id"] == "call_001"]
    assert len(bash_results) == 1, (
        f"Expected 1 tool_result for call_001, got: {tool_result_blocks}"
    )
    content = bash_results[0]["content"]
    assert isinstance(content, list), (
        f"Expected structured contents array, got {type(content).__name__}: {content}"
    )
    assert len(content) == 1, f"Expected 1 content item, got: {content}"
    assert content[0]["type"] == "terminal", (
        f"Expected terminal content type, got: {content[0]}"
    )
    assert content[0]["exitCode"] == 0, f"Expected exitCode 0, got: {content[0]}"


def test_copilot_failed_tool_preserves_structured_contents():
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    failed_bash = [b for b in tool_result_blocks if b["tool_use_id"] == "call_004"]
    assert len(failed_bash) == 1, (
        f"Expected 1 tool_result for call_004, got: {tool_result_blocks}"
    )
    block = failed_bash[0]
    assert block["is_error"] is True, f"Expected is_error=true, got: {block}"
    content = block["content"]
    assert isinstance(content, list), (
        f"Expected structured contents array for failed tool, "
        f"got {type(content).__name__}: {content}"
    )
    assert len(content) == 1, f"Expected 1 content item, got: {content}"
    assert content[0]["type"] == "terminal", (
        f"Expected terminal content type, got: {content[0]}"
    )
    assert content[0]["exitCode"] == 1, f"Expected exitCode 1, got: {content[0]}"


def test_copilot_recoverable_session_error_does_not_fail_turn():
    events, blocks = _run_showcase_scenario()

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1, (
        f"Expected exactly 1 result event, got {len(result_events)}: {result_events}"
    )
    assert result_events[0]["subtype"] == "success", (
        f"Expected result subtype 'success' despite mid-turn session.error, "
        f"got: {result_events[0]}"
    )


def test_copilot_fatal_session_error_fails_turn():
    proc = _start_executor()
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "__fatal_session_error__"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=10)
        _send_command(proc, {"type": "stop"})

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1, (
            f"Expected exactly 1 result event, got {len(result_events)}: {result_events}"
        )
        assert result_events[0]["subtype"] == "error_during_execution", (
            f"Expected error_during_execution for fatal session.error, "
            f"got: {result_events[0]}"
        )
        assert "WebSocket connection closed" in result_events[0]["errors"][0], (
            f"Expected error message from session.error, got: {result_events[0]['errors']}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_empty_turn_is_error():
    proc = _start_executor()
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "__empty_turn__"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=10)
        _send_command(proc, {"type": "stop"})

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1, (
            f"Expected exactly 1 result event, got {len(result_events)}: {result_events}"
        )
        assert result_events[0]["subtype"] == "error_during_execution", (
            f"Expected error_during_execution for empty turn, got: {result_events[0]}"
        )
        assert "without an assistant message" in result_events[0]["errors"][0], (
            f"Expected descriptive error, got: {result_events[0]['errors']}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
