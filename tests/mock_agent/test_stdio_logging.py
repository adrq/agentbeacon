"""Test stdio protocol logging integration."""

import os
from pathlib import Path
from unittest.mock import patch


def test_bracketed_format_logging_stdio():
    """Test bracketed format logging in stdio mode."""
    test_name = "test_stdio_bracketed"

    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": f"tests/stdio::{test_name}"}):
        from agentmaestro.mock_agent.stdio_mode import process_input

        # Mock input with bracketed format
        input_text = "[exec_stdio][node_stdio] NOW Handle stdio task"

        # Process input (should log before returning response)
        result = process_input(input_text)

        # Verify response is generated
        assert isinstance(result, dict)
        assert "taskStatus" in result or "artifacts" in result

        # Verify log file was created and contains entry
        expected_log_file = Path("logs/tests_stdio__test_stdio_bracketed.log")
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1
        assert "[exec_stdio][node_stdio]" in lines[0]
        assert "Handle stdio task" in lines[0]
        assert "NOW" not in lines[0]  # Should be replaced with timestamp

        # Keep log file for manual verification


def test_plain_text_logging_stdio():
    """Test plain text logging in stdio mode."""
    test_name = "test_stdio_plain"

    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": f"tests/stdio::{test_name}"}):
        from agentmaestro.mock_agent.stdio_mode import process_input

        # Mock input with plain text
        input_text = "plain task without brackets"

        # Process input (should log before returning response)
        result = process_input(input_text)

        # Verify response is generated
        assert isinstance(result, dict)

        # Verify log file was created with default values
        expected_log_file = Path("logs/tests_stdio__test_stdio_plain.log")
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1
        assert "[default][default]" in lines[0]
        assert "plain task without brackets" in lines[0]

        # Keep log file for manual verification


def test_special_commands_integration_stdio():
    """Test integration with existing special commands."""
    test_name = "test_stdio_special_commands"

    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": f"tests/stdio::{test_name}"}):
        from agentmaestro.mock_agent.stdio_mode import process_input

        # Test DELAY command with bracketed format (should complete and log)
        input_text = "[exec_delay][node_delay] NOW DELAY_1"

        # Process input
        result = process_input(input_text)

        # Special commands should still work and log
        assert isinstance(result, dict)

        # Verify logging happened
        expected_log_file = Path("logs/tests_stdio__test_stdio_special_commands.log")
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        assert "[exec_delay][node_delay]" in content
        assert "DELAY_1" in content

        # Keep log file for manual verification


def test_multiple_stdio_requests_same_test():
    """Test multiple stdio requests in same test create multiple log entries."""
    test_name = "test_stdio_multiple"

    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": f"tests/stdio::{test_name}"}):
        from agentmaestro.mock_agent.stdio_mode import process_input

        # Process multiple inputs
        process_input("[exec_multi][node_1] NOW First task")
        process_input("[exec_multi][node_2] NOW Second task")
        process_input("[exec_multi][node_3] NOW Third task")

        # Verify all entries in same log file
        expected_log_file = Path("logs/tests_stdio__test_stdio_multiple.log")
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 3

        assert "[exec_multi][node_1]" in lines[0] and "First task" in lines[0]
        assert "[exec_multi][node_2]" in lines[1] and "Second task" in lines[1]
        assert "[exec_multi][node_3]" in lines[2] and "Third task" in lines[2]

        # Keep log file for manual verification
