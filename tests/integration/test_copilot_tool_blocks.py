"""Integration tests for Copilot executor tool_use and tool_result blocks.

Verifies that the copilot-executor correctly emits:
- tool_use blocks with id, name, and input (arguments) from tool.execution_start
- tool_result blocks with tool_use_id, content, and is_error from tool.execution_complete

Uses AGENTBEACON_MOCK_SDK=1 so no real Copilot auth is needed.

Run with: uv run pytest tests/integration/test_copilot_tool_blocks.py -v
"""

import json
import os
import subprocess
import threading

EXECUTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "executors", "dist")
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")


def _start_executor():
    """Spawn the copilot-executor with mock SDK enabled."""
    script = os.path.join(EXECUTORS_DIR, "copilot-executor.js")
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
    assert got_result.is_set(), (
        f"Executor did not emit a terminal 'result' event within {timeout}s "
        f"(got {len(events)} events: {[e.get('type') for e in events]})"
    )
    return events


def _extract_content_blocks(events):
    """Extract all content blocks from message events."""
    blocks = []
    for e in events:
        if e.get("type") == "message":
            for block in e.get("content", []):
                blocks.append(block)
    return blocks


def _run_showcase_scenario():
    """Run the mock showcase scenario and return all content blocks."""
    proc = _start_executor()
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
        return events, _extract_content_blocks(events)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_copilot_emits_tool_use_blocks():
    """tool.execution_start events produce tool_use content blocks."""
    events, blocks = _run_showcase_scenario()

    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    assert len(tool_use_blocks) >= 2, (
        f"Expected at least 2 tool_use blocks (Bash + Read), got {len(tool_use_blocks)}: "
        f"{[b.get('type') for b in blocks]}"
    )

    # Each tool_use block should have id and name
    for block in tool_use_blocks:
        assert "id" in block, f"tool_use block missing 'id': {block}"
        assert "name" in block, f"tool_use block missing 'name': {block}"


def test_copilot_emits_tool_result_blocks():
    """tool.execution_complete events produce tool_result content blocks."""
    events, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) >= 2, (
        f"Expected at least 2 tool_result blocks, got {len(tool_result_blocks)}: "
        f"{[b.get('type') for b in blocks]}"
    )

    # Each tool_result block should have tool_use_id
    for block in tool_result_blocks:
        assert "tool_use_id" in block, (
            f"tool_result block missing 'tool_use_id': {block}"
        )


def test_copilot_tool_result_has_correct_tool_use_id():
    """tool_result blocks reference the correct tool_use_id from their paired tool_use block."""
    _, blocks = _run_showcase_scenario()

    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]

    tool_use_ids = {b["id"] for b in tool_use_blocks}
    tool_result_refs = {b["tool_use_id"] for b in tool_result_blocks}

    # Every tool_result should reference a tool_use that was emitted
    assert tool_result_refs.issubset(tool_use_ids), (
        f"tool_result references {tool_result_refs} but tool_use IDs are {tool_use_ids}"
    )
    # And vice versa — every tool_use should have a matching result
    assert tool_use_ids == tool_result_refs, (
        f"Mismatch: tool_use IDs {tool_use_ids} != tool_result refs {tool_result_refs}"
    )


def test_copilot_tool_use_includes_input():
    """tool_use blocks include the 'input' field when arguments are available."""
    _, blocks = _run_showcase_scenario()

    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    assert len(tool_use_blocks) >= 1, "Expected at least 1 tool_use block"

    # The mock SDK provides arguments for both tools
    blocks_with_input = [b for b in tool_use_blocks if b.get("input") is not None]
    assert len(blocks_with_input) >= 2, (
        f"Expected at least 2 tool_use blocks with input, got {len(blocks_with_input)}: "
        f"{tool_use_blocks}"
    )

    # Verify the Bash tool has the expected argument structure
    bash_blocks = [b for b in tool_use_blocks if b.get("name") == "Bash"]
    assert len(bash_blocks) >= 1, (
        f"Expected Bash tool_use block, got: {tool_use_blocks}"
    )
    assert "command" in bash_blocks[0]["input"], (
        f"Bash tool_use input should have 'command': {bash_blocks[0]}"
    )


def test_copilot_tool_result_includes_content():
    """tool_result blocks include content from the SDK's result field."""
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) >= 1, "Expected at least 1 tool_result block"

    # Mock SDK provides result.content for both tools
    blocks_with_content = [
        b for b in tool_result_blocks if b.get("content") not in (None, "")
    ]
    assert len(blocks_with_content) >= 2, (
        f"Expected at least 2 tool_result blocks with content, got {len(blocks_with_content)}: "
        f"{tool_result_blocks}"
    )


def test_copilot_successful_tool_not_marked_error():
    """Successful tool completions have is_error=false."""
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) >= 1, "Expected at least 1 tool_result block"

    # Successful tools (call_001, call_002) should have is_error=false
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
    """Failed tool completions (success: false) have is_error=true."""
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
    """Failed tool_result content uses error.message from the SDK event."""
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    # call_003 completes with success=false and an error.message field
    failed_blocks = [b for b in tool_result_blocks if b["tool_use_id"] == "call_003"]
    assert len(failed_blocks) == 1, (
        f"Expected 1 tool_result for call_003, got: {tool_result_blocks}"
    )
    assert failed_blocks[0]["content"] == "Permission denied: /etc/readonly-file", (
        f"Expected error message in content, got: {failed_blocks[0]}"
    )


def test_copilot_structured_contents_preserved():
    """tool_result preserves structured contents array (terminal, images, etc.)."""
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    # call_001 (Bash) has a structured contents array with terminal output
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
    """Failed tools with structured contents preserve them over the flat error message."""
    _, blocks = _run_showcase_scenario()

    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    # call_004 (Bash) fails with exit code 1 but has structured terminal output
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
    """A mid-turn session.error (e.g. permission_denied) does not abort the turn.

    The mock scenario dispatches a recoverable session.error between tool
    executions. The turn should still complete with subtype 'success' because
    session.error is non-fatal per the SDK lifecycle — session.idle follows.
    """
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
    """A fatal session.error (e.g. connection_closed) immediately fails the turn.

    The mock emits session.error with errorType 'connection_closed' and no
    session.idle. The executor should reject promptly with error_during_execution
    rather than hanging until the Rust inactivity timer.
    """
    proc = _start_executor()
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "__fatal_session_error__",
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
    """A turn that reaches session.idle without any assistant.message is an error.

    The mock goes straight to session.idle with no assistant.message event.
    The executor should report error_during_execution, not false-positive success.
    """
    proc = _start_executor()
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "__empty_turn__",
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
