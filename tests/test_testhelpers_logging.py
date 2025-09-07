"""Test testhelpers integration with file logging system."""

import os
from pathlib import Path
from unittest.mock import patch


def test_parse_agent_log_reads_and_parses_correctly():
    """Test parse_agent_log() function reads and parses log files correctly."""
    from tests.testhelpers import parse_agent_log

    # Create a test log file with known content
    test_name = "test_parse_reads_correctly"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    # Write test log entries
    test_entries = [
        "[exec_123][node_1] 2025-09-24T10:30:15Z First task execution",
        "[exec_123][node_2] 2025-09-24T10:31:22Z Second task execution",
        "[workflow-run-456][data-processor] 2025-09-24T10:32:33Z Complex ID task",
    ]

    log_file.write_text("\n".join(test_entries) + "\n")

    # Parse using testhelpers function
    entries = parse_agent_log(test_name)

    # Verify correct parsing
    assert len(entries) == 3

    # Check first entry
    assert entries[0]["execution_id"] == "exec_123"
    assert entries[0]["node_id"] == "node_1"
    assert entries[0]["timestamp"] == "2025-09-24T10:30:15Z"
    assert entries[0]["task_text"] == "First task execution"

    # Check second entry
    assert entries[1]["execution_id"] == "exec_123"
    assert entries[1]["node_id"] == "node_2"
    assert entries[1]["timestamp"] == "2025-09-24T10:31:22Z"
    assert entries[1]["task_text"] == "Second task execution"

    # Check complex ID entry
    assert entries[2]["execution_id"] == "workflow-run-456"
    assert entries[2]["node_id"] == "data-processor"
    assert entries[2]["timestamp"] == "2025-09-24T10:32:33Z"
    assert entries[2]["task_text"] == "Complex ID task"

    # Keep log file for manual verification


def test_parse_agent_log_handles_missing_files_gracefully():
    """Test parse_agent_log handles missing log files gracefully."""
    from tests.testhelpers import parse_agent_log

    # Try to parse non-existent log file
    entries = parse_agent_log("non_existent_test")

    # Should return empty list, not raise exception
    assert isinstance(entries, list)
    assert len(entries) == 0


def test_parse_agent_log_handles_empty_files():
    """Test parse_agent_log handles empty log files."""
    from tests.testhelpers import parse_agent_log

    # Create empty log file
    test_name = "test_empty_file"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)
    log_file.write_text("")

    # Parse empty file
    entries = parse_agent_log(test_name)

    # Should return empty list
    assert isinstance(entries, list)
    assert len(entries) == 0

    # Keep log file for manual verification


def test_parse_agent_log_reuses_unified_parse_function():
    """Test that parse_agent_log reuses the same unified parse_agent_entry function."""
    from tests.testhelpers import parse_agent_log
    from agentmaestro.mock_agent.file_logger import parse_agent_entry

    # Create test log file
    test_name = "test_unified_function"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    # Write test entry
    test_entry = (
        "[exec_unified][node_unified] 2025-09-24T10:30:15Z Test unified parsing"
    )
    log_file.write_text(test_entry + "\n")

    # Parse using both functions
    testhelper_entries = parse_agent_log(test_name)
    direct_parse = parse_agent_entry(test_entry)

    # Results should be identical (same parsing logic)
    assert len(testhelper_entries) == 1
    entry_from_helper = testhelper_entries[0]

    assert entry_from_helper["execution_id"] == direct_parse["execution_id"]
    assert entry_from_helper["node_id"] == direct_parse["node_id"]
    assert entry_from_helper["timestamp"] == direct_parse["timestamp"]
    assert entry_from_helper["task_text"] == direct_parse["task_text"]

    # Keep log file for manual verification


def test_parse_agent_log_handles_malformed_lines():
    """Test parse_agent_log handles malformed log lines gracefully."""
    from tests.testhelpers import parse_agent_log

    # Create test log file with mix of good and malformed lines
    test_name = "test_malformed_lines"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    # Mix of good and bad lines
    test_entries = [
        "[exec_good][node_good] 2025-09-24T10:30:15Z Good entry",
        "malformed line without brackets",
        "[exec_good][node_good2] 2025-09-24T10:31:22Z Another good entry",
        "",  # Empty line
        "[incomplete brackets",
        "[exec_good][node_good3] 2025-09-24T10:32:33Z Final good entry",
    ]

    log_file.write_text("\n".join(test_entries) + "\n")

    # Parse file
    entries = parse_agent_log(test_name)

    # Should parse all lines (malformed ones get default values)
    assert len(entries) == 6

    # Good entries should be parsed correctly
    assert entries[0]["execution_id"] == "exec_good"
    assert entries[0]["node_id"] == "node_good"

    # Malformed entries should get defaults
    assert entries[1]["execution_id"] == "default"
    assert entries[1]["node_id"] == "default"
    assert entries[1]["task_text"] == "malformed line without brackets"

    # Keep log file for manual verification


def test_parse_agent_log_integration_with_actual_logging():
    """Test integration between actual logging and testhelpers parsing."""
    test_name = "test_integration_with_logging"

    with patch.dict(
        os.environ, {"PYTEST_CURRENT_TEST": f"tests/integration::{test_name}"}
    ):
        from agentmaestro.mock_agent.file_logger import log_task_completion
        from tests.testhelpers import parse_agent_log

        # Use actual logging function to create entries
        log_task_completion("[exec_integration][node_1] NOW First integration test")
        log_task_completion("[exec_integration][node_2] NOW Second integration test")
        log_task_completion("Plain text without brackets")

        # Parse using testhelpers
        entries = parse_agent_log("tests_integration__test_integration_with_logging")

        # Verify integration works
        assert len(entries) == 3

        # Check that NOW was replaced with actual timestamps
        assert entries[0]["timestamp"] != "NOW"
        assert entries[1]["timestamp"] != "NOW"
        assert entries[2]["timestamp"] != "NOW"

        # Check task text is preserved
        assert entries[0]["task_text"] == "First integration test"
        assert entries[1]["task_text"] == "Second integration test"
        assert entries[2]["task_text"] == "Plain text without brackets"

        # Check execution/node IDs
        assert entries[0]["execution_id"] == "exec_integration"
        assert entries[0]["node_id"] == "node_1"
        assert entries[1]["execution_id"] == "exec_integration"
        assert entries[1]["node_id"] == "node_2"
        assert entries[2]["execution_id"] == "default"  # Plain text fallback
        assert entries[2]["node_id"] == "default"

        # Keep log file for manual verification


def test_parse_agent_log_performance_large_files():
    """Test parse_agent_log performance with larger log files."""
    from tests.testhelpers import parse_agent_log

    # Create larger test log file
    test_name = "test_large_file_performance"
    log_file = Path(f"logs/{test_name}.log")
    log_file.parent.mkdir(exist_ok=True)

    # Generate 1000 log entries
    test_entries = []
    for i in range(1000):
        entry = f"[exec_perf][node_{i:04d}] 2025-09-24T10:{i // 60:02d}:{i % 60:02d}Z Performance test entry {i}"
        test_entries.append(entry)

    log_file.write_text("\n".join(test_entries) + "\n")

    # Parse large file
    entries = parse_agent_log(test_name)

    # Verify all entries parsed
    assert len(entries) == 1000

    # Spot check a few entries
    assert entries[0]["node_id"] == "node_0000"
    assert entries[500]["node_id"] == "node_0500"
    assert entries[999]["node_id"] == "node_0999"

    # All should have same execution_id
    exec_ids = {entry["execution_id"] for entry in entries}
    assert exec_ids == {"exec_perf"}

    # Keep log file for manual verification
