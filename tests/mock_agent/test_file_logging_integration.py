"""Test file logging integration for mock agent."""

import os
from pathlib import Path
from unittest.mock import patch
import pytest


def test_automatic_log_file_creation_with_pytest_current_test():
    """Test automatic log file creation using PYTEST_CURRENT_TEST."""
    test_name = "test_automatic_creation"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        # This should create logs/tests_test_file__test_automatic_creation.log
        log_task_completion("[exec_123][node_1] NOW Initialize system")

        # Check that file was created
        expected_log_file = Path("logs/tests_test_file__test_automatic_creation.log")
        assert expected_log_file.exists()

        # Read and verify content
        content = expected_log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1
        assert "[exec_123][node_1]" in lines[0]
        assert "Initialize system" in lines[0]
        assert "NOW" not in lines[0]  # Should be replaced with timestamp

        # Keep log file for manual verification


def test_log_entry_format_with_actual_timestamps():
    """Test log entry format includes actual ISO timestamps."""
    test_name = "test_timestamp_format"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        log_task_completion("[exec_456][node_2] NOW Process data")

        expected_log_file = Path("logs/tests_test_file__test_timestamp_format.log")
        content = expected_log_file.read_text()
        lines = content.strip().split("\n")

        # Parse timestamp from log entry
        line = lines[0]
        # Format: [exec_456][node_2] 2025-09-24T10:30:15Z Process data
        parts = line.split(" ", 2)
        timestamp_str = parts[1]  # 2025-09-24T10:30:15Z

        # Verify timestamp format (ISO with Z suffix)
        assert timestamp_str.endswith("Z")
        assert "T" in timestamp_str
        assert len(timestamp_str) == 20  # 2025-09-24T10:30:15Z

        # Keep log file for manual verification


def test_file_locking_with_concurrent_access_simulation():
    """Test file locking mechanism with simulated concurrent access."""
    test_name = "test_concurrent_access"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        # Simulate multiple rapid writes
        log_task_completion("[exec_001][node_a] NOW Task A")
        log_task_completion("[exec_001][node_b] NOW Task B")
        log_task_completion("[exec_001][node_c] NOW Task C")

        expected_log_file = Path("logs/tests_test_file__test_concurrent_access.log")
        content = expected_log_file.read_text()
        lines = content.strip().split("\n")

        # Should have exactly 3 lines, no corruption
        assert len(lines) == 3
        assert "Task A" in lines[0]
        assert "Task B" in lines[1]
        assert "Task C" in lines[2]

        # Each line should be properly formatted
        for line in lines:
            assert line.startswith("[exec_001][node_")
            assert " NOW " not in line  # Timestamps should be replaced
            assert "Task " in line

        # Keep log file for manual verification


def test_directory_creation_when_missing():
    """Test that missing logs directory gets created automatically."""
    test_name = "test_directory_creation"

    # Remove logs directory if it exists
    logs_dir = Path("logs")
    if logs_dir.exists():
        import shutil

        shutil.rmtree(logs_dir)

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        # Should create directory and succeed
        log_task_completion("[exec_999][node_test] NOW Test with missing directory")

        # Verify directory was created
        assert logs_dir.exists()

        expected_log_file = logs_dir / "tests_test_file__test_directory_creation.log"
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        assert "Test with missing directory" in content


def test_no_exceptions_raised_on_logging_failures():
    """Test that log_task_completion never raises exceptions."""
    test_name = "test_no_exceptions"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        # These should not raise exceptions even if they fail internally
        try:
            log_task_completion("[exec_error][node_test] NOW Error handling test")
            log_task_completion("")  # Empty string
            log_task_completion("Plain text without brackets")
        except Exception as e:
            pytest.fail(f"log_task_completion should not raise exceptions: {e}")


def test_backward_compatibility_plain_text():
    """Test backward compatibility with plain text prompts."""
    test_name = "test_backward_compatibility"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        log_task_completion("Just a plain text task without brackets")

        expected_log_file = Path(
            "logs/tests_test_file__test_backward_compatibility.log"
        )
        content = expected_log_file.read_text()
        lines = content.strip().split("\n")

        # Should use default values
        line = lines[0]
        assert "[default][default]" in line
        assert "Just a plain text task without brackets" in line

        # Keep log file for manual verification
