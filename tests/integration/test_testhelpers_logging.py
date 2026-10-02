# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from pathlib import Path
from unittest.mock import patch


def test_parse_agent_log_reads_and_parses_correctly():
    from tests.mock_agent_helpers import parse_agent_log

    test_name = "test_parse_reads_correctly"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    test_entries = [
        "[exec_123][node_1] 2025-09-24T10:30:15Z First task execution",
        "[exec_123][node_2] 2025-09-24T10:31:22Z Second task execution",
        "[workflow-run-456][data-processor] 2025-09-24T10:32:33Z Complex ID task",
    ]

    log_file.write_text("\n".join(test_entries) + "\n")

    entries = parse_agent_log(test_name)

    assert len(entries) == 3

    assert entries[0]["execution_id"] == "exec_123"
    assert entries[0]["node_id"] == "node_1"
    assert entries[0]["timestamp"] == "2025-09-24T10:30:15Z"
    assert entries[0]["task_text"] == "First task execution"

    assert entries[1]["execution_id"] == "exec_123"
    assert entries[1]["node_id"] == "node_2"
    assert entries[1]["timestamp"] == "2025-09-24T10:31:22Z"
    assert entries[1]["task_text"] == "Second task execution"

    assert entries[2]["execution_id"] == "workflow-run-456"
    assert entries[2]["node_id"] == "data-processor"
    assert entries[2]["timestamp"] == "2025-09-24T10:32:33Z"
    assert entries[2]["task_text"] == "Complex ID task"


def test_parse_agent_log_handles_missing_files_gracefully():
    from tests.mock_agent_helpers import parse_agent_log

    entries = parse_agent_log("non_existent_test")

    assert isinstance(entries, list)
    assert len(entries) == 0


def test_parse_agent_log_handles_empty_files():
    from tests.mock_agent_helpers import parse_agent_log

    test_name = "test_empty_file"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)
    log_file.write_text("")

    entries = parse_agent_log(test_name)

    assert isinstance(entries, list)
    assert len(entries) == 0


def test_parse_agent_log_reuses_unified_parse_function():
    from tests.mock_agent_helpers import parse_agent_log
    from agentbeacon.mock_agent.file_logger import parse_agent_entry

    test_name = "test_unified_function"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    test_entry = (
        "[exec_unified][node_unified] 2025-09-24T10:30:15Z Test unified parsing"
    )
    log_file.write_text(test_entry + "\n")

    testhelper_entries = parse_agent_log(test_name)
    direct_parse = parse_agent_entry(test_entry)

    assert len(testhelper_entries) == 1
    entry_from_helper = testhelper_entries[0]

    assert entry_from_helper["execution_id"] == direct_parse["execution_id"]
    assert entry_from_helper["node_id"] == direct_parse["node_id"]
    assert entry_from_helper["timestamp"] == direct_parse["timestamp"]
    assert entry_from_helper["task_text"] == direct_parse["task_text"]


def test_parse_agent_log_handles_malformed_lines():
    from tests.mock_agent_helpers import parse_agent_log

    test_name = "test_malformed_lines"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    test_entries = [
        "[exec_good][node_good] 2025-09-24T10:30:15Z Good entry",
        "malformed line without brackets",
        "[exec_good][node_good2] 2025-09-24T10:31:22Z Another good entry",
        "",
        "[incomplete brackets",
        "[exec_good][node_good3] 2025-09-24T10:32:33Z Final good entry",
    ]

    log_file.write_text("\n".join(test_entries) + "\n")

    entries = parse_agent_log(test_name)

    assert len(entries) == 6

    assert entries[0]["execution_id"] == "exec_good"
    assert entries[0]["node_id"] == "node_good"

    assert entries[1]["execution_id"] == "default"
    assert entries[1]["node_id"] == "default"
    assert entries[1]["task_text"] == "malformed line without brackets"


def test_parse_agent_log_integration_with_actual_logging():
    test_name = "test_integration_with_logging"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/integration::{test_name}"}
    ):
        from agentbeacon.mock_agent.file_logger import log_task_completion
        from tests.mock_agent_helpers import parse_agent_log

        Path("logs/tests_integration__test_integration_with_logging.log").unlink(
            missing_ok=True
        )

        log_task_completion("[exec_integration][node_1] NOW First integration test")
        log_task_completion("[exec_integration][node_2] NOW Second integration test")
        log_task_completion("Plain text without brackets")

        entries = parse_agent_log("tests_integration__test_integration_with_logging")

        assert len(entries) == 3

        assert entries[0]["timestamp"] != "NOW"
        assert entries[1]["timestamp"] != "NOW"
        assert entries[2]["timestamp"] != "NOW"

        assert entries[0]["task_text"] == "First integration test"
        assert entries[1]["task_text"] == "Second integration test"
        assert entries[2]["task_text"] == "Plain text without brackets"

        assert entries[0]["execution_id"] == "exec_integration"
        assert entries[0]["node_id"] == "node_1"
        assert entries[1]["execution_id"] == "exec_integration"
        assert entries[1]["node_id"] == "node_2"
        assert entries[2]["execution_id"] == "default"
        assert entries[2]["node_id"] == "default"


def test_parse_agent_log_performance_large_files():
    from tests.mock_agent_helpers import parse_agent_log

    test_name = "test_large_file_performance"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    test_entries = []
    for i in range(1000):
        entry = f"[exec_perf][node_{i:04d}] 2025-09-24T10:{i // 60:02d}:{i % 60:02d}Z Performance test entry {i}"
        test_entries.append(entry)

    log_file.write_text("\n".join(test_entries) + "\n")

    entries = parse_agent_log(test_name)

    assert len(entries) == 1000

    assert entries[0]["node_id"] == "node_0000"
    assert entries[500]["node_id"] == "node_0500"
    assert entries[999]["node_id"] == "node_0999"

    exec_ids = {entry["execution_id"] for entry in entries}
    assert exec_ids == {"exec_perf"}
