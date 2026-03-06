"""Verify SDK orchestration tool blocking (KI-75).

Spawns Node.js executor processes with AGENTBEACON_MOCK_SDK=1 and verifies
that disallowedTools/excludedTools are configured in the SDK options.
"""

import json
import os
import subprocess
import threading

EXECUTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "executors", "dist")
NODE_PATH = os.environ.get("AGENTBEACON_NODE_PATH", "node")

CLAUDE_DISALLOWED_TOOLS = [
    "Agent",
    "Task",
    "TaskOutput",
    "TaskStop",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "SendMessage",
    "SendMessageTool",
]

COPILOT_EXCLUDED_TOOLS = [
    "task",
    "read_agent",
    "list_agents",
]


def _start_executor(script_name):
    """Spawn a Node.js executor process with mock SDK enabled."""
    script = os.path.join(EXECUTORS_DIR, script_name)
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


def _run_session(script_name, timeout=30):
    """Start executor, send a single turn, wait for result, return events + stderr."""
    proc = _start_executor(script_name)
    try:
        stdout_events = []
        stderr_lines = []
        got_result = threading.Event()

        def stdout_reader():
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stdout_events.append(event)
                if event.get("type") == "result":
                    got_result.set()

        def stderr_reader():
            for line in proc.stderr:
                stderr_lines.append(line.strip())

        t_out = threading.Thread(target=stdout_reader, daemon=True)
        t_err = threading.Thread(target=stderr_reader, daemon=True)
        t_out.start()
        t_err.start()

        _send_command(
            proc,
            {
                "type": "start",
                "prompt": "hello",
                "cwd": os.getcwd(),
            },
        )
        got_result.wait(timeout=timeout)
        _send_command(proc, {"type": "stop"})

        # Close stdin to deliver EOF so the main loop exits cleanly
        proc.stdin.close()

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        return stdout_events, stderr_lines
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_executor_blocks_orchestration_tools():
    """Claude executor passes disallowedTools to SDK options."""
    stdout_events, stderr_lines = _run_session("claude-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) >= 1

    stderr_text = "\n".join(stderr_lines)
    assert "disallowedTools=" in stderr_text, (
        f"Mock Claude SDK did not log disallowedTools. stderr:\n{stderr_text}"
    )

    for line in stderr_lines:
        if "disallowedTools=" in line:
            json_part = line.split("disallowedTools=", 1)[1]
            logged_tools = json.loads(json_part)
            assert set(logged_tools) == set(CLAUDE_DISALLOWED_TOOLS)
            break
    else:
        raise AssertionError(
            "no stderr line contained disallowedTools= with parseable JSON"
        )

    assert "WARNING: no disallowedTools" not in stderr_text


def test_copilot_executor_blocks_orchestration_tools():
    """Copilot executor passes excludedTools to SDK session config."""
    stdout_events, stderr_lines = _run_session("copilot-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) >= 1

    stderr_text = "\n".join(stderr_lines)
    assert "excludedTools=" in stderr_text, (
        f"Mock Copilot SDK did not log excludedTools. stderr:\n{stderr_text}"
    )

    for line in stderr_lines:
        if "excludedTools=" in line:
            json_part = line.split("excludedTools=", 1)[1]
            logged_tools = json.loads(json_part)
            assert set(logged_tools) == set(COPILOT_EXCLUDED_TOOLS)
            break
    else:
        raise AssertionError(
            "no stderr line contained excludedTools= with parseable JSON"
        )

    assert "WARNING: no excludedTools" not in stderr_text
