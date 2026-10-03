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


def _spawn_claude_executor(extra_env: dict[str, str]) -> subprocess.Popen:
    script = os.path.join(MOCK_EXECUTORS_DIR, "claude-executor.js")
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
    env.update(extra_env)
    return subprocess.Popen(
        [NODE_PATH, script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _send_command(proc: subprocess.Popen, cmd: dict) -> None:
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()


def _collect_events(proc: subprocess.Popen, timeout: int = 30) -> list[dict]:
    events: list[dict] = []
    done = threading.Event()

    def reader() -> None:
        while True:
            line = proc.stdout.readline()
            if not line:
                done.set()
                return
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") in ("result", "error"):
                done.set()
                return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    return events


def _run_scenario(env_var: str) -> list[dict]:
    proc = _spawn_claude_executor({env_var: "1"})
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "trigger scenario"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})
        return events
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _content_blocks(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in events:
        if e.get("type") != "message":
            continue
        for block in e.get("content") or []:
            if isinstance(block, dict):
                out.append(block)
    return out


def _messages(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "message"]


def test_auth_status_event_is_captured_raw():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_AUTH_FAILURE")
    blocks = _content_blocks(events)

    auth_blocks = [b for b in blocks if b.get("type") == "auth_status"]
    assert len(auth_blocks) == 1, (
        f"Expected exactly 1 auth_status block, got {len(auth_blocks)}: {blocks}"
    )
    auth = auth_blocks[0]
    assert auth.get("isAuthenticating") is False
    assert auth.get("error") == "Invalid API key"
    assert auth.get("output") == [
        "auth_token=REDACTED_TOKEN_VALUE",
        "Authentication failed",
    ]


def test_assistant_error_emitted_immediately_without_message_stop():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_AUTH_FAILURE")
    blocks = _content_blocks(events)

    data_blocks = [
        b.get("data")
        for b in blocks
        if isinstance(b.get("data"), dict) and b["data"].get("type") == "assistant"
    ]
    assert len(data_blocks) == 1, (
        f"Expected exactly 1 assistant data block, got {len(data_blocks)}: {blocks}"
    )
    assistant_data = data_blocks[0]
    assert assistant_data.get("error") == "authentication_failed"
    content = assistant_data.get("message", {}).get("content", [])
    assert len(content) > 0, f"Expected non-empty message.content, got: {content}"
    assert content[0].get("text") == "Authentication error occurred."

    text_blocks = [
        b
        for b in blocks
        if b.get("type") == "text" and "Authentication" in (b.get("text") or "")
    ]
    assert text_blocks == [], (
        f"Buffered assistant text leaked through failure path: {text_blocks}"
    )

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0].get("subtype") == "error_during_execution"


def test_unhandled_system_subtype_persisted_as_raw_block():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_SYSTEM_SUBTYPE")
    msgs = _messages(events)

    api_retry_msgs = [
        m
        for m in msgs
        if any(
            (b.get("type") == "system" and b.get("subtype") == "api_retry")
            for b in (m.get("content") or [])
        )
    ]
    assert len(api_retry_msgs) == 1, (
        f"Expected exactly 1 message carrying system.api_retry, got "
        f"{len(api_retry_msgs)}. All blocks: {_content_blocks(events)}"
    )
    msg = api_retry_msgs[0]
    assert msg.get("ephemeral") is not True, (
        "expected system.api_retry to be non-ephemeral, got ephemeral=True"
    )
    block = next(b for b in msg["content"] if b.get("subtype") == "api_retry")
    assert block.get("attempt") == 1
    assert block.get("max_retries") == 3
    assert block.get("error") == "rate_limit"

    text_blocks = [b for b in _content_blocks(events) if b.get("type") == "text"]
    assert any("Retried successfully" in (b.get("text") or "") for b in text_blocks), (
        f"Echo response missing: {text_blocks}"
    )

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0].get("subtype") == "success"


def test_executor_populates_result_fallback_from_tracked_assistant_text():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_SYSTEM_SUBTYPE")

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1, (
        f"Expected exactly 1 result event, got {len(result_events)}: {result_events}"
    )
    result = result_events[0]
    assert result.get("subtype") == "success"
    assert result.get("result") == "Retried successfully.", (
        f"expected result.result to be the assistant text, got: {result.get('result')!r}"
    )

    msgs = _messages(events)
    final_output_msgs = [m for m in msgs if "finalOutput" in m]
    assert final_output_msgs == [], (
        f"unexpected finalOutput in message events: {final_output_msgs}"
    )


def test_ephemeral_messages_do_not_populate_result_fallback():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_DELTA_SUBTYPE")

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    result = result_events[0]
    assert result.get("subtype") == "success"
    assert result.get("result") == "Delta test done."
    assert "file_path" not in (result.get("result") or ""), (
        "ephemeral input_json_delta fragments leaked into result.result"
    )


def test_input_json_delta_subtype_is_ephemeral():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_DELTA_SUBTYPE")
    msgs = _messages(events)

    delta_msgs = [
        m
        for m in msgs
        if any(b.get("type") == "input_json_delta" for b in (m.get("content") or []))
    ]
    assert len(delta_msgs) == 2, (
        f"Expected 2 input_json_delta messages, got {len(delta_msgs)}"
    )
    for m in delta_msgs:
        assert m.get("ephemeral") is True, (
            f"expected input_json_delta message to be ephemeral, got: {m}"
        )

    fragments = [
        b.get("partial_json")
        for m in delta_msgs
        for b in m["content"]
        if b.get("type") == "input_json_delta"
    ]
    assert fragments == ['{"file_path": "/tmp/test', '.txt"}']


def test_tool_progress_emitted_as_ephemeral():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_TOOL_PROGRESS")
    msgs = _messages(events)

    progress_msgs = [
        m
        for m in msgs
        if any(b.get("type") == "tool_progress" for b in (m.get("content") or []))
    ]
    assert len(progress_msgs) == 2, (
        f"Expected 2 tool_progress messages, got {len(progress_msgs)}"
    )
    for m in progress_msgs:
        assert m.get("ephemeral") is True, (
            f"expected tool_progress to be ephemeral, got: {m}"
        )

    tool_use_msgs = [
        m
        for m in msgs
        if any(b.get("type") == "tool_use" for b in (m.get("content") or []))
    ]
    assert len(tool_use_msgs) == 1
    assert tool_use_msgs[0].get("ephemeral") is not True

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0].get("subtype") == "success"


def test_model_refusal_fallback_persisted_as_raw_block():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_REFUSAL_FALLBACK")
    msgs = _messages(events)

    fallback_msgs = [
        m
        for m in msgs
        if any(
            (b.get("type") == "system" and b.get("subtype") == "model_refusal_fallback")
            for b in (m.get("content") or [])
        )
    ]
    assert len(fallback_msgs) == 1
    msg = fallback_msgs[0]
    assert msg.get("ephemeral") is not True
    block = next(
        b for b in msg["content"] if b.get("subtype") == "model_refusal_fallback"
    )
    assert block.get("trigger") == "refusal"
    assert block.get("original_model") == "claude-sonnet-5"
    assert block.get("fallback_model") == "claude-haiku-4-5"
    assert block.get("api_refusal_category") == "cyber"
    assert block.get("request_id") == "req-mock-fallback-1"
    assert block.get("uuid") == "uuid-fallback-1"
    assert block.get("retracted_message_uuids") == ["uuid-refused-1", "uuid-refused-2"]

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0].get("subtype") == "success"


def test_conversation_reset_persisted_as_raw_block():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_REFUSAL_FALLBACK")
    msgs = _messages(events)

    reset_msgs = [
        m
        for m in msgs
        if any(b.get("type") == "conversation_reset" for b in (m.get("content") or []))
    ]
    assert len(reset_msgs) == 1
    assert reset_msgs[0].get("ephemeral") is not True
    block = next(
        b for b in reset_msgs[0]["content"] if b.get("type") == "conversation_reset"
    )
    assert block.get("new_conversation_id") == "conv-mock-reset-1"
    assert block.get("uuid") == "uuid-reset-1"

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0].get("subtype") == "success"


def test_active_goal_persisted_as_raw_block():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_REFUSAL_FALLBACK")
    msgs = _messages(events)

    goal_msgs = [
        m
        for m in msgs
        if any(b.get("type") == "active_goal" for b in (m.get("content") or []))
    ]
    assert len(goal_msgs) == 1
    assert goal_msgs[0].get("ephemeral") is not True
    block = next(b for b in goal_msgs[0]["content"] if b.get("type") == "active_goal")
    assert block.get("uuid") == "uuid-goal-1"
    assert block.get("value", {}).get("set_at") == 1700000000000
    assert block.get("value", {}).get("tokens_at_start") == 1234

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0].get("subtype") == "success"
