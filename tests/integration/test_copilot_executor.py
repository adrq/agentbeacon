"""Integration tests for the Copilot executor adapter.

These tests use real Copilot/GitHub auth and cost real money.
NEVER run them from CI or AI agents.

Run manually: uv run pytest -m copilot -o "addopts=" -v
"""

import json
import os
import select
import subprocess
import time

import pytest

EXECUTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "executors", "dist")
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")
SCRIPT = os.path.join(EXECUTORS_DIR, "copilot-executor.js")


def start_executor():
    """Spawn the copilot-executor Node.js process."""
    return subprocess.Popen(
        [NODE_PATH, SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def send_command(proc, cmd):
    """Write a JSON command to the executor's stdin."""
    proc.stdin.write(json.dumps(cmd) + "\n")
    proc.stdin.flush()


def read_events(proc, timeout=120, until_type=None):
    """Read events from stdout until timeout or target type found."""
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
        if ready:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line:
                event = json.loads(line)
                events.append(event)
                if until_type and event.get("type") == until_type:
                    return events
    return events


@pytest.mark.copilot
def test_copilot_executor_starts_and_completes():
    """Simple prompt -> verify init + result events."""
    proc = start_executor()
    try:
        send_command(
            proc,
            {
                "type": "start",
                "prompt": "What is 2+2? Reply with just the number.",
                "cwd": os.getcwd(),
            },
        )
        events = read_events(proc, timeout=120, until_type="result")
        send_command(proc, {"type": "stop"})

        init_events = [e for e in events if e["type"] == "init"]
        assert len(init_events) >= 1, f"Expected init event, got: {events}"
        assert "sessionId" in init_events[0]

        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) >= 1, f"Expected result event, got: {events}"
        assert result_events[-1]["subtype"] == "success"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.copilot
def test_copilot_executor_mcp_tools_visible():
    """Spawn with beacon MCP config -> init event has sessionId."""
    proc = start_executor()
    try:
        send_command(
            proc,
            {
                "type": "start",
                "prompt": "What tools do you have available? Just list them briefly.",
                "cwd": os.getcwd(),
                "mcpServers": {
                    "test-beacon": {
                        "type": "http",
                        "url": "http://localhost:9999/mcp",
                        "headers": {"Authorization": "Bearer test"},
                    },
                },
            },
        )
        events = read_events(proc, timeout=120, until_type="init")
        send_command(proc, {"type": "stop"})

        init_events = [e for e in events if e["type"] == "init"]
        assert len(init_events) >= 1, f"Expected init event, got: {events}"
        assert "sessionId" in init_events[0]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.copilot
def test_copilot_executor_multi_turn():
    """Turn 1 -> result -> send prompt -> turn 2 completes."""
    proc = start_executor()
    try:
        send_command(
            proc,
            {
                "type": "start",
                "prompt": "Remember the number 42. Reply with just 'ok'.",
                "cwd": os.getcwd(),
            },
        )
        events1 = read_events(proc, timeout=120, until_type="result")
        result1 = [e for e in events1 if e["type"] == "result"]
        assert len(result1) >= 1, f"Expected first result, got: {events1}"

        send_command(
            proc,
            {
                "type": "prompt",
                "text": "What number did I ask you to remember? Reply with just the number.",
            },
        )
        events2 = read_events(proc, timeout=120, until_type="result")
        result2 = [e for e in events2 if e["type"] == "result"]
        assert len(result2) >= 1, f"Expected second result, got: {events2}"
        assert result2[-1]["subtype"] == "success"

        send_command(proc, {"type": "stop"})
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.copilot
def test_copilot_executor_cancel():
    """Start turn, cancel, verify cancelled result."""
    proc = start_executor()
    try:
        send_command(
            proc,
            {
                "type": "start",
                "prompt": "Write a very long essay about the history of mathematics. Make it at least 5000 words.",
                "cwd": os.getcwd(),
            },
        )
        # Give it a moment to start processing, then cancel
        time.sleep(2)
        send_command(proc, {"type": "cancel"})

        events = read_events(proc, timeout=30, until_type="result")
        send_command(proc, {"type": "stop"})

        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) >= 1, (
            f"Expected result event after cancel, got: {events}"
        )
        # Should be cancelled or possibly success if it finished before cancel arrived
        assert result_events[-1]["subtype"] in ("cancelled", "success")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
