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
    "ListAgents",
    "ReadNotifications",
    "Workflow",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
    "Monitor",
    "REPL",
    "RemoteTrigger",
    "PushNotification",
    "ProposeGoal",
    "EnterPlanMode",
    "ExitPlanMode",
    "EnterWorktree",
    "ExitWorktree",
    "AskUserQuestion",
    "Artifact",
    "SendFeedback",
    "ClaudeDesign",
    "DesignSync",
    "Projects",
    "ProposeSkills",
    "ShowOnboardingRolePicker",
    "ReportFindings",
]

COPILOT_EXCLUDED_TOOLS = [
    "task",
    "read_agent",
    "list_agents",
]


def _start_executor(script_name):
    script = os.path.join(MOCK_EXECUTORS_DIR, script_name)
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


def _run_session(script_name, timeout=30, start_overrides=None):
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

        start_cmd = {
            "type": "start",
            "parts": [{"text": "hello"}],
            "cwd": os.getcwd(),
        }
        if start_overrides:
            start_cmd.update(start_overrides)
        _send_command(proc, start_cmd)
        got_result.wait(timeout=timeout)
        _send_command(proc, {"type": "stop"})

        proc.stdin.close()

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        return stdout_events, stderr_lines
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_claude_executor_blocks_orchestration_tools():
    stdout_events, stderr_lines = _run_session("claude-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

    stderr_text = "\n".join(stderr_lines)
    assert "disallowedTools=" in stderr_text, (
        f"Mock Claude SDK did not log disallowedTools. stderr:\n{stderr_text}"
    )

    for line in stderr_lines:
        if "disallowedTools=" in line:
            json_part = line.split("disallowedTools=", 1)[1]
            logged_tools = json.loads(json_part)
            assert set(logged_tools) == set(CLAUDE_DISALLOWED_TOOLS)
            assert len(logged_tools) == len(set(logged_tools)), (
                "Production disallowedTools contains duplicates"
            )
            break
    else:
        raise AssertionError(
            "no stderr line contained disallowedTools= with parseable JSON"
        )

    assert "WARNING: no disallowedTools" not in stderr_text


def test_claude_executor_disables_auto_memory():
    stdout_events, stderr_lines = _run_session("claude-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

    stderr_text = "\n".join(stderr_lines)

    settings_line = next((line for line in stderr_lines if "] settings=" in line), None)
    assert settings_line is not None, (
        f"Mock Claude SDK did not log settings. stderr:\n{stderr_text}"
    )
    settings = json.loads(settings_line.split("settings=", 1)[1])
    assert settings["autoMemoryEnabled"] is False
    assert settings["autoDreamEnabled"] is False
    assert settings["disableArtifact"] is True
    assert settings["feedbackDrafts"] == "off"
    assert settings["workflowKeywordTriggerEnabled"] is False

    env_line = next(
        (line for line in stderr_lines if "disableAutoMemoryEnv=" in line), None
    )
    assert env_line is not None, (
        f"Mock Claude SDK did not log disableAutoMemoryEnv. stderr:\n{stderr_text}"
    )
    assert env_line.split("disableAutoMemoryEnv=", 1)[1] == "1"

    path_line = next((line for line in stderr_lines if "envHasPath=" in line), None)
    assert path_line is not None, (
        f"Mock Claude SDK did not log envHasPath. stderr:\n{stderr_text}"
    )
    assert path_line.split("envHasPath=", 1)[1] == "true"


def test_copilot_executor_blocks_orchestration_tools():
    stdout_events, stderr_lines = _run_session("copilot-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

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


def test_disallowed_list_has_no_duplicates():
    assert len(CLAUDE_DISALLOWED_TOOLS) == len(set(CLAUDE_DISALLOWED_TOOLS))


def _parse_kv_json(stderr_lines, key):
    line = next((line for line in stderr_lines if f"] {key}=" in line), None)
    assert line is not None, (
        f"Mock Claude SDK did not log {key}. stderr:\n" + "\n".join(stderr_lines)
    )
    return json.loads(line.split(f"{key}=", 1)[1])


def test_claude_executor_disables_claude_ai_connectors():
    stdout_events, stderr_lines = _run_session("claude-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

    settings = _parse_kv_json(stderr_lines, "settings")
    assert settings["disableClaudeAiConnectors"] is True


def test_claude_executor_defaults_thinking_to_adaptive_summarized():
    stdout_events, stderr_lines = _run_session("claude-executor.js")

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

    thinking = _parse_kv_json(stderr_lines, "thinking")
    assert thinking == {"type": "adaptive", "display": "summarized"}

    settings = _parse_kv_json(stderr_lines, "settings")
    assert "showThinkingSummaries" not in settings


def test_claude_executor_respects_thinking_disabled_override():
    stdout_events, stderr_lines = _run_session(
        "claude-executor.js", start_overrides={"thinking": {"type": "disabled"}}
    )

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

    thinking = _parse_kv_json(stderr_lines, "thinking")
    assert thinking == {"type": "disabled"}


def test_claude_executor_respects_thinking_budget_override():
    override = {"type": "enabled", "budgetTokens": 2048}
    stdout_events, stderr_lines = _run_session(
        "claude-executor.js", start_overrides={"thinking": override}
    )

    result_events = [e for e in stdout_events if e["type"] == "result"]
    assert len(result_events) == 1

    thinking = _parse_kv_json(stderr_lines, "thinking")
    assert thinking == override
