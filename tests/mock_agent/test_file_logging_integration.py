# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Test file logging integration for mock agent."""

import os
from pathlib import Path
from unittest.mock import patch
import pytest


def test_automatic_log_file_creation_with_pytest_current_test():
    test_name = "test_automatic_creation"

    expected_log_file = Path("logs/tests_test_file__test_automatic_creation.log")
    expected_log_file.unlink(missing_ok=True)

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        log_task_completion("[exec_123][node_1] NOW Initialize system")

        expected_log_file = Path("logs/tests_test_file__test_automatic_creation.log")
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1
        assert "[exec_123][node_1]" in lines[0]
        assert "Initialize system" in lines[0]
        assert "NOW" not in lines[0]


def test_log_entry_format_with_actual_timestamps():
    test_name = "test_timestamp_format"

    Path("logs/tests_test_file__test_timestamp_format.log").unlink(missing_ok=True)

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        log_task_completion("[exec_456][node_2] NOW Process data")

        expected_log_file = Path("logs/tests_test_file__test_timestamp_format.log")
        content = expected_log_file.read_text()
        lines = content.strip().split("\n")

        line = lines[0]
        parts = line.split(" ", 2)
        timestamp_str = parts[1]

        assert timestamp_str.endswith("Z")
        assert "T" in timestamp_str
        assert len(timestamp_str) == 20


def test_file_locking_with_concurrent_access_simulation():
    test_name = "test_concurrent_access"

    Path("logs/tests_test_file__test_concurrent_access.log").unlink(missing_ok=True)

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        log_task_completion("[exec_001][node_a] NOW Task A")
        log_task_completion("[exec_001][node_b] NOW Task B")
        log_task_completion("[exec_001][node_c] NOW Task C")

        expected_log_file = Path("logs/tests_test_file__test_concurrent_access.log")
        content = expected_log_file.read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 3
        assert "Task A" in lines[0]
        assert "Task B" in lines[1]
        assert "Task C" in lines[2]

        for line in lines:
            assert line.startswith("[exec_001][node_")
            assert " NOW " not in line
            assert "Task " in line


def test_directory_creation_when_missing():
    test_name = "test_directory_creation"

    logs_dir = Path("logs")
    if logs_dir.exists():
        import shutil

        shutil.rmtree(logs_dir)

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        log_task_completion("[exec_999][node_test] NOW Test with missing directory")

        assert logs_dir.exists()

        expected_log_file = logs_dir / "tests_test_file__test_directory_creation.log"
        assert expected_log_file.exists()

        content = expected_log_file.read_text()
        assert "Test with missing directory" in content


def test_no_exceptions_raised_on_logging_failures():
    test_name = "test_no_exceptions"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/test_file::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion

        try:
            log_task_completion("[exec_error][node_test] NOW Error handling test")
            log_task_completion("")
            log_task_completion("Plain text without brackets")
        except Exception as e:
            pytest.fail(f"log_task_completion should not raise exceptions: {e}")


def test_plain_text_entry_uses_default_ids():
    test_name = "test_backward_compatibility"

    Path("logs/tests_test_file__test_backward_compatibility.log").unlink(
        missing_ok=True
    )

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

        line = lines[0]
        assert "[default][default]" in line
        assert "Just a plain text task without brackets" in line
