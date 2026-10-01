# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Test unified parsing function for mock agent file logging."""

from agentbeacon.mock_agent.file_logger import parse_agent_entry


def test_bracketed_format_parsing_basic():
    line = "[exec_123][node_1] NOW task text"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "exec_123"
    assert result["node_id"] == "node_1"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == "task text"


def test_bracketed_format_with_iso_timestamp():
    line = "[exec_123][node_1] 2025-09-24T10:30:15Z Analyze data"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "exec_123"
    assert result["node_id"] == "node_1"
    assert result["timestamp"] == "2025-09-24T10:30:15Z"
    assert result["task_text"] == "Analyze data"


def test_dash_safe_parsing_complex_ids():
    line = "[workflow-run-123][data-node-v2] NOW complex task with spaces"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "workflow-run-123"
    assert result["node_id"] == "data-node-v2"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == "complex task with spaces"


def test_fallback_for_non_bracketed_format():
    line = "plain task text without brackets"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "default"
    assert result["node_id"] == "default"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == "plain task text without brackets"


def test_special_commands_fallback():
    line = "HANG"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "default"
    assert result["node_id"] == "default"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == "HANG"


def test_empty_string_handling():
    line = ""
    result = parse_agent_entry(line)

    assert result["execution_id"] == "default"
    assert result["node_id"] == "default"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == ""


def test_malformed_brackets_fallback():
    line = "[exec_123 missing closing bracket"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "default"
    assert result["node_id"] == "default"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == "[exec_123 missing closing bracket"


def test_single_bracket_pair_fallback():
    line = "[exec_123] missing second pair"
    result = parse_agent_entry(line)

    assert result["execution_id"] == "default"
    assert result["node_id"] == "default"
    assert result["timestamp"] == "NOW"
    assert result["task_text"] == "[exec_123] missing second pair"
