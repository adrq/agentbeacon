"""Integration tests for Claude executor transient error retry logic.

Uses mock SDK with AGENTBEACON_MOCK_SDK_TRANSIENT_FAILURES to simulate
SDK initialization failures (AxiosError timeouts).

Run with: uv run pytest tests/integration/test_sdk_retry.py -v
"""

import json
import os
import subprocess
import threading
import time

EXECUTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "executors", "dist")
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")


def _start_executor(transient_failures=0):
    """Spawn claude-executor with mock SDK and optional transient failures."""
    script = os.path.join(EXECUTORS_DIR, "claude-executor.js")
    env = os.environ.copy()
    env["AGENTBEACON_MOCK_SDK"] = "1"
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
    """Read events until result or error type, or timeout."""
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
    """Executor retries on transient AxiosError and eventually succeeds."""
    proc = _start_executor(transient_failures=1)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
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
    """Executor retries twice (max) and succeeds on third attempt."""
    proc = _start_executor(transient_failures=2)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
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
    """Executor emits error after exhausting all retry attempts."""
    proc = _start_executor(transient_failures=10)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
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
    """Executor works normally when no transient failures configured."""
    proc = _start_executor(transient_failures=0)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
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
    """Executor does NOT retry transient errors when resumeSessionId is set.

    Resume sessions risk duplicate side effects if retried, because a client-side
    failure before the init event does not prove the server didn't accept the prompt.
    """
    proc = _start_executor(transient_failures=1)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
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
    """Cancel sent during retry backoff prevents next attempt from starting.

    The mock throws synchronously on transient failure, so the 1s retry delay
    begins almost immediately. We send cancel 200ms later — it should abort
    the controller during the delay and produce a cancelled result without
    starting a new query().
    """
    proc = _start_executor(transient_failures=1)
    try:
        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
                "cwd": os.getcwd(),
            },
        )
        # Attempt 0 fails immediately (synchronous throw in mock).
        # Retry delay (1s) starts. Send cancel during it.
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
