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


def _start_executor(transient_failures=0):
    script = os.path.join(MOCK_EXECUTORS_DIR, "claude-executor.js")
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
    if transient_failures > 0:
        env["AGENTBEACON_MOCK_SDK_TRANSIENT_FAILURES"] = str(transient_failures)
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
    done = threading.Event()

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
            if event.get("type") in ("result", "error"):
                done.set()
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    return events


def test_retry_succeeds_after_transient_failure():
    proc = _start_executor(transient_failures=1)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "hello"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        types = [e["type"] for e in events]
        assert "init" in types, f"Expected init event after retry, got: {types}"
        assert "result" in types, f"Expected result event after retry, got: {types}"

        result = next(e for e in events if e["type"] == "result")
        assert result["subtype"] == "success"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_retry_succeeds_after_two_transient_failures():
    proc = _start_executor(transient_failures=2)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "hello"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        types = [e["type"] for e in events]
        assert "init" in types, f"Expected init event after 2 retries, got: {types}"
        assert "result" in types, f"Expected result event, got: {types}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_retries_exhausted_emits_error():
    proc = _start_executor(transient_failures=10)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "hello"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        types = [e["type"] for e in events]
        assert "error" in types, (
            f"Expected error event after exhausted retries, got: {types}"
        )
        assert "init" not in types, f"Should not have init event, got: {types}"

        error = next(e for e in events if e["type"] == "error")
        assert "AxiosError" in error["message"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_no_retry_without_transient_failures():
    proc = _start_executor(transient_failures=0)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "hello"}],
                "cwd": os.getcwd(),
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        types = [e["type"] for e in events]
        assert "init" in types
        assert "result" in types
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_no_retry_on_resume_session():
    proc = _start_executor(transient_failures=1)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "hello"}],
                "cwd": os.getcwd(),
                "resumeSessionId": "sdk-session-previous-123",
            },
        )
        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        types = [e["type"] for e in events]
        assert "error" in types, f"Expected error (no retry for resume), got: {types}"
        assert "init" not in types, (
            f"Should not have retried and reached init, got: {types}"
        )

        error = next(e for e in events if e["type"] == "error")
        assert "AxiosError" in error["message"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_cancel_during_retry_delay():
    proc = _start_executor(transient_failures=1)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "parts": [{"text": "hello"}],
                "cwd": os.getcwd(),
            },
        )
        time.sleep(0.2)
        _send_command(proc, {"type": "cancel"})

        events = _collect_events(proc, timeout=30)
        _send_command(proc, {"type": "stop"})

        types = [e["type"] for e in events]
        assert "result" in types, f"Expected cancelled result, got: {types}"
        assert "init" not in types, f"Should not have started new query, got: {types}"

        result = next(e for e in events if e["type"] == "result")
        assert result["subtype"] == "cancelled"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
