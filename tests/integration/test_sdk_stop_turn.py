import json
import os
import subprocess
import threading
import time


EXECUTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "executors", "dist")
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")


def _start_executor(script_name, extra_env=None):
    script = os.path.join(EXECUTORS_DIR, script_name)
    env = os.environ.copy()
    env["AGENTBEACON_MOCK_SDK"] = "1"
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


def _assert_stop_turn_result_without_messages(events):
    result_events = [e for e in events if e.get("type") == "result"]
    assert result_events, f"Expected result event, got: {events}"
    assert result_events[-1]["subtype"] == "stopped_by_user"
    assert events[-1] == result_events[-1]
    message_events = [e for e in events[:-1] if e.get("type") == "message"]
    assert not message_events, (
        "Expected buffered stop_turn to prevent assistant/tool output before "
        f"stopped_by_user, got: {events}"
    )


def _assert_followup_prompt_succeeds(
    proc, text="Continue after stop", expected_assistant_text=None
):
    _send_commands(
        proc,
        [{"type": "prompt", "parts": [{"kind": "text", "text": text}]}],
    )
    events = _collect_until_result(proc)
    result_events = [e for e in events if e.get("type") == "result"]
    assert result_events, f"Expected follow-up result event, got: {events}"
    assert result_events[-1]["subtype"] == "success", (
        "Expected executor session to remain reusable after stopped_by_user, "
        f"got: {events}"
    )
    if expected_assistant_text is not None:
        assistant_text = " ".join(
            block.get("text", "")
            for event in events
            if event.get("type") == "message" and event.get("role") == "assistant"
            for block in event.get("content", [])
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        assert expected_assistant_text in assistant_text, (
            "Expected follow-up success to correspond to the newly submitted prompt, "
            f"got: {events}"
        )
    return events


def _assert_immediate_stop_turn(script_name, extra_env=None):
    proc = _start_executor(script_name, extra_env=extra_env)
    stderr_output = ""
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "Do work"}],
                    "cwd": os.getcwd(),
                },
                {"type": "stop_turn"},
            ],
        )
        events = _collect_until_result(proc)
        _assert_stop_turn_result_without_messages(events)
        _assert_followup_prompt_succeeds(proc)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        stderr_output = proc.stderr.read()

    if (
        script_name == "claude-executor.js"
        and extra_env
        and extra_env.get("AGENTBEACON_MOCK_CLAUDE_INIT_DELAY_MS")
    ):
        assert stderr_output.count("[mock-claude-sdk] query() called") == 1, (
            "Expected buffered stop_turn before Claude init to skip the initial SDK query, "
            f"got stderr: {stderr_output}"
        )


def _assert_prebuffered_stop_turn(script_name):
    proc = _start_executor(
        script_name,
        extra_env={"AGENTBEACON_MOCK_COPILOT_STARTUP_DELAY_MS": "50"},
    )
    stderr_output = ""
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "Do work"}],
                    "cwd": os.getcwd(),
                },
                {"type": "stop_turn"},
            ],
        )
        events = _collect_until_result(proc)
        _assert_stop_turn_result_without_messages(events)
        _assert_followup_prompt_succeeds(proc)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        stderr_output = proc.stderr.read()

    if script_name == "copilot-executor.js":
        assert 'prompt="Do work"' not in stderr_output, (
            "Expected buffered stop_turn to prevent initial Copilot send(), "
            f"got stderr: {stderr_output}"
        )
        assert 'prompt="Continue after stop"' in stderr_output, (
            "Expected follow-up prompt to still execute after buffered stop_turn, "
            f"got stderr: {stderr_output}"
        )


def test_claude_buffers_stop_turn_before_abort_handle_exists():
    _assert_immediate_stop_turn("claude-executor.js")


def test_claude_buffers_stop_turn_before_init_exists():
    _assert_immediate_stop_turn(
        "claude-executor.js",
        extra_env={"AGENTBEACON_MOCK_CLAUDE_INIT_DELAY_MS": "50"},
    )


def test_copilot_buffers_stop_turn_before_session_exists():
    _assert_prebuffered_stop_turn("copilot-executor.js")


def test_copilot_buffers_stop_turn_before_followup_send_runs():
    proc = _start_executor("copilot-executor.js")
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
        first_turn_events = _collect_until_result(proc)
        first_turn_results = [
            event for event in first_turn_events if event.get("type") == "result"
        ]
        assert first_turn_results, (
            f"Expected first-turn result, got: {first_turn_events}"
        )
        assert first_turn_results[-1]["subtype"] == "success"

        _send_commands(
            proc,
            [
                {"type": "prompt", "parts": [{"kind": "text", "text": "More work"}]},
                {"type": "stop_turn"},
            ],
        )
        followup_events = _collect_until_result(proc)
        _assert_stop_turn_result_without_messages(followup_events)
        _assert_followup_prompt_succeeds(proc, text="Resume after follow-up stop")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_buffers_stop_turn_before_followup_prompt_runs():
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
        first_turn_events = _collect_until_result(proc)
        first_turn_results = [
            event for event in first_turn_events if event.get("type") == "result"
        ]
        assert first_turn_results, (
            f"Expected first-turn result, got: {first_turn_events}"
        )
        assert first_turn_results[-1]["subtype"] == "success"

        _send_commands(
            proc,
            [
                {"type": "prompt", "parts": [{"kind": "text", "text": "More work"}]},
                {"type": "stop_turn"},
            ],
        )
        followup_events = _collect_until_result(proc)
        _assert_stop_turn_result_without_messages(followup_events)
        _assert_followup_prompt_succeeds(
            proc, text="Resume after Claude follow-up stop"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _assert_buffered_prompt_queue_is_drained_on_stop(script_name):
    proc = _start_executor(script_name)
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
        first_turn_events = _collect_until_result(proc)
        first_turn_results = [
            event for event in first_turn_events if event.get("type") == "result"
        ]
        assert first_turn_results, (
            f"Expected first-turn result, got: {first_turn_events}"
        )
        assert first_turn_results[-1]["subtype"] == "success"

        _send_commands(
            proc,
            [
                {
                    "type": "prompt",
                    "parts": [{"kind": "text", "text": "Queued follow-up 1"}],
                },
                {
                    "type": "prompt",
                    "parts": [{"kind": "text", "text": "Queued follow-up 2"}],
                },
                {"type": "stop_turn"},
            ],
        )
        stopped_events = _collect_until_result(proc)
        _assert_stop_turn_result_without_messages(stopped_events)

        expected_assistant_text = None
        if script_name == "copilot-executor.js":
            expected_assistant_text = "Prompt after drained stop"

        _assert_followup_prompt_succeeds(
            proc,
            text="Prompt after drained stop",
            expected_assistant_text=expected_assistant_text,
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_drops_already_buffered_prompts_on_stop():
    _assert_buffered_prompt_queue_is_drained_on_stop("claude-executor.js")


def test_copilot_drops_already_buffered_prompts_on_stop():
    _assert_buffered_prompt_queue_is_drained_on_stop("copilot-executor.js")


def test_copilot_send_rejection_does_not_wait_for_idle():
    proc = _start_executor("copilot-executor.js")
    try:
        _send_commands(
            proc,
            [
                {
                    "type": "start",
                    "parts": [{"kind": "text", "text": "__send_reject__"}],
                    "cwd": os.getcwd(),
                }
            ],
        )
        events = _collect_until_result(proc)
        result_events = [e for e in events if e.get("type") == "result"]
        assert result_events, f"Expected result event, got: {events}"
        assert result_events[-1]["subtype"] == "error_during_execution", (
            "Expected send rejection to surface immediately without waiting for session.idle, "
            f"got: {events}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _assert_stale_stop_between_turns_is_ignored(script_name, extra_env=None):
    proc = _start_executor(script_name, extra_env=extra_env)
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
        first_turn_events = _collect_until_result(proc)
        first_turn_results = [
            event for event in first_turn_events if event.get("type") == "result"
        ]
        assert first_turn_results, (
            f"Expected first-turn result, got: {first_turn_events}"
        )
        assert first_turn_results[-1]["subtype"] == "success"

        _send_commands(proc, [{"type": "stop_turn"}])
        time.sleep(0.05)

        _assert_followup_prompt_succeeds(proc, text="Work after stale stop")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_ignores_stale_stop_between_turns():
    _assert_stale_stop_between_turns_is_ignored("claude-executor.js")


def test_copilot_ignores_stale_stop_between_turns():
    _assert_stale_stop_between_turns_is_ignored("copilot-executor.js")
