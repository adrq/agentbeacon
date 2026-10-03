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

NEEDLE = "Summary of changes"
FENCE = "example -> output"


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


def _messages(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "message"]


def _nonstreamed_text_messages(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in _messages(events):
        if m.get("ephemeral") is True:
            continue
        for block in m.get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and NEEDLE in (block.get("text") or "")
            ):
                out.append(m)
                break
    return out


def test_nonstreamed_assistant_text_is_persisted_exactly_once():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_NONSTREAMED_TURN")

    text_msgs = _nonstreamed_text_messages(events)
    assert len(text_msgs) == 1, (
        f"Expected exactly 1 persisted assistant message with the non-streamed "
        f"text, got {len(text_msgs)}. All messages: {_messages(events)}"
    )

    text = next(
        b["text"]
        for b in text_msgs[0]["content"]
        if isinstance(b, dict)
        and b.get("type") == "text"
        and NEEDLE in b.get("text", "")
    )
    assert FENCE in text, f"Fenced code block content missing from flush: {text!r}"


def test_nonstreamed_result_backfills_result_from_flushed_text():
    events = _run_scenario("AGENTBEACON_MOCK_SDK_NONSTREAMED_TURN")

    result_events = [e for e in events if e.get("type") == "result"]
    assert len(result_events) == 1, (
        f"Expected exactly 1 result event, got {len(result_events)}: {result_events}"
    )
    result = result_events[0]
    assert result.get("subtype") == "success"
    assert NEEDLE in (result.get("result") or ""), (
        f"expected result.result to contain the flushed non-streamed text, "
        f"got: {result.get('result')!r}"
    )
