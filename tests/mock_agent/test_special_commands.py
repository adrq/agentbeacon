"""Integration tests for special commands (HANG, DELAY, FAIL).

These tests validate special test command functionality across all modes:
- HANG: 1-hour sleep command for timeout testing
- DELAY_X: Controlled delay commands (1, 2, 3, 5, 1500ms)
- FAIL_NODE: Immediate failure with exit code
- FAIL_ONCE: Intermittent failure based on time parity

Tests cover behavior in stdio, A2A, and ACP modes.
"""

import subprocess
import time
from pathlib import Path

import httpx

from .conftest import send_stdio_input, send_a2a_message
from tests.testhelpers import parse_agent_log, get_current_test_name


def test_delay_commands_timing(mock_agent_stdio, mock_agent_a2a):
    """Test special delay commands with timing verification across modes."""
    # Test key delay values in stdio mode
    delay_tests = [("DELAY_1", 1.0), ("DELAY_3", 3.0), ("DELAY_1500", 1.5)]

    for command, expected_delay in delay_tests:
        start_time = time.time()
        output = send_stdio_input(mock_agent_stdio, command)
        duration = time.time() - start_time

        assert duration >= expected_delay
        assert output["taskStatus"]["state"] == "completed"

    # Test A2A mode - submission should be fast, execution happens async
    start_time = time.time()
    task = send_a2a_message(mock_agent_a2a, "DELAY_1")
    submission_duration = time.time() - start_time

    # Task submission should be fast (async)
    assert submission_duration < 1.0
    assert task["status"]["state"] in ["submitted", "working"]

    # Poll for completion to verify delay actually occurred
    task_id = task["id"]
    poll_start = time.time()

    while time.time() - poll_start < 5.0:  # 5 second timeout
        get_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "id": 2,
            "params": {"taskId": task_id},
        }
        updated_task = send_a2a_message(
            mock_agent_a2a, "", payload_override=get_payload
        )

        if updated_task["status"]["state"] == "completed":
            total_duration = time.time() - poll_start
            assert total_duration >= 1.0  # Delay should have occurred
            break

        time.sleep(0.1)
    else:
        # Task should complete within timeout
        assert False, f"Task {task_id} did not complete within 5 seconds"


def test_fail_node_stdio_mode(mock_agent_stdio):
    """Test FAIL_NODE command returns failure format in stdio mode."""
    output = send_stdio_input(mock_agent_stdio, "FAIL_NODE")

    assert output["taskStatus"]["state"] == "failed"
    assert "message" in output["taskStatus"]
    assert output["taskStatus"]["message"]["role"] == "assistant"
    assert "Mock agent failure" in output["taskStatus"]["message"]["parts"][0]["text"]


def test_fail_node_a2a_mode(mock_agent_a2a):
    """Test FAIL_NODE command returns failed task in A2A mode."""
    task = send_a2a_message(mock_agent_a2a, "FAIL_NODE")
    task_id = task["id"]

    # Initial submission should be fast
    assert task["status"]["state"] in ["submitted", "working", "failed"]

    # Poll until task reaches failed state
    poll_start = time.time()
    final_task = None

    while time.time() - poll_start < 3.0:  # 3 second timeout
        get_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "id": 2,
            "params": {"taskId": task_id},
        }
        final_task = send_a2a_message(mock_agent_a2a, "", payload_override=get_payload)

        if final_task["status"]["state"] in ["failed", "completed"]:
            break

        time.sleep(0.1)
    else:
        assert False, f"Task {task_id} did not complete within 3 seconds"

    # Verify task failed as expected
    assert final_task["status"]["state"] == "failed"
    assert "history" in final_task
    # Should have error message in task history or status


def test_hang_command_starts_long_task_a2a(mock_agent_a2a):
    """Test HANG command creates long-running task in A2A mode."""
    start_time = time.time()
    task = send_a2a_message(mock_agent_a2a, "HANG")
    creation_duration = time.time() - start_time

    # Task creation should be fast
    assert creation_duration < 1.0

    # Task should be in submitted or working state (not completed)
    assert task["status"]["state"] in ["submitted", "working"]

    # Verify task is still running after a short wait
    time.sleep(2)
    get_request = {
        "jsonrpc": "2.0",
        "method": "tasks/get",
        "id": 2,
        "params": {"taskId": task["id"]},
    }
    response = httpx.post(f"{mock_agent_a2a}/rpc", json=get_request)
    updated_task = response.json()["result"]

    # Should still be working (hang lasts 1 hour)
    assert updated_task["status"]["state"] in ["working", "submitted"]


def test_hang_command_can_be_cancelled_a2a(mock_agent_a2a):
    """Test HANG command task can be cancelled in A2A mode."""
    # Start hang task
    task = send_a2a_message(mock_agent_a2a, "HANG")

    # Cancel it
    cancel_request = {
        "jsonrpc": "2.0",
        "method": "tasks/cancel",
        "id": 3,
        "params": {"taskId": task["id"]},
    }
    response = httpx.post(f"{mock_agent_a2a}/rpc", json=cancel_request)
    cancelled_task = response.json()["result"]

    assert cancelled_task["status"]["state"] == "canceled"


def test_hang_command_stdio_timeout(mock_agent_stdio):
    """Test HANG command in stdio mode (with process timeout)."""
    # This test verifies hang behavior but with a short timeout
    # to avoid waiting the full hour

    start_time = time.time()

    # Send HANG command
    mock_agent_stdio.stdin.write("HANG\n")
    mock_agent_stdio.stdin.flush()

    # Wait briefly then terminate to avoid long test
    time.sleep(2)
    mock_agent_stdio.terminate()

    try:
        mock_agent_stdio.wait(timeout=5)
    except subprocess.TimeoutExpired:
        mock_agent_stdio.kill()
        mock_agent_stdio.wait()

    duration = time.time() - start_time

    # Should have run for at least the wait time
    assert duration >= 2.0


def test_cross_mode_consistency(mock_agent_stdio, mock_agent_a2a):
    """Test special commands behave consistently across stdio and A2A modes."""
    # Test FAIL_NODE consistency - both modes should ultimately fail
    stdio_output = send_stdio_input(mock_agent_stdio, "FAIL_NODE")
    a2a_task = send_a2a_message(mock_agent_a2a, "FAIL_NODE")

    # Stdio should fail immediately
    assert stdio_output["taskStatus"]["state"] == "failed"

    # A2A should eventually fail - poll for final state
    task_id = a2a_task["id"]
    poll_start = time.time()

    while time.time() - poll_start < 3.0:
        get_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "id": 3,
            "params": {"taskId": task_id},
        }
        final_a2a_task = send_a2a_message(
            mock_agent_a2a, "", payload_override=get_payload
        )

        if final_a2a_task["status"]["state"] in ["failed", "completed"]:
            break
        time.sleep(0.1)

    assert final_a2a_task["status"]["state"] == "failed"

    # Test delay consistency - both modes should take ~1 second
    start_time = time.time()
    stdio_output = send_stdio_input(mock_agent_stdio, "DELAY_1")
    stdio_duration = time.time() - start_time

    # Stdio should block for the delay
    assert stdio_duration >= 1.0
    assert stdio_output["taskStatus"]["state"] == "completed"

    # A2A should complete within reasonable time when polled
    a2a_task = send_a2a_message(mock_agent_a2a, "DELAY_1")
    task_id = a2a_task["id"]
    poll_start = time.time()

    while time.time() - poll_start < 3.0:
        get_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "id": 4,
            "params": {"taskId": task_id},
        }
        final_a2a_task = send_a2a_message(
            mock_agent_a2a, "", payload_override=get_payload
        )

        if final_a2a_task["status"]["state"] == "completed":
            break
        time.sleep(0.1)

    # Both modes should ultimately complete successfully
    assert final_a2a_task["status"]["state"] == "completed"


def test_special_commands_logging_behavior(mock_agent_stdio):
    """Test that special commands produce expected logging behavior."""

    # Get current test name for log file lookup
    test_name = get_current_test_name("test_special_commands_logging_behavior")

    # Clear any existing log entries for this test
    log_file = Path(f"logs/{test_name}.log")
    if log_file.exists():
        log_file.unlink()

    # Test DELAY_X commands produce log entries after delay
    start_time = time.time()
    output = send_stdio_input(mock_agent_stdio, "DELAY_1")
    duration = time.time() - start_time

    # Verify delay occurred and task completed
    assert duration >= 1.0, f"DELAY_1 should take at least 1 second, took {duration}"
    assert output["taskStatus"]["state"] == "completed"

    # Verify DELAY command created exactly one log entry
    delay_log_entries = parse_agent_log(test_name)
    assert len(delay_log_entries) == 1, (
        f"DELAY_1 should create exactly 1 log entry, found {len(delay_log_entries)} entries"
    )

    # Test FAIL_NODE command produces no log entry (returns failure without process termination)
    fail_output = send_stdio_input(mock_agent_stdio, "FAIL_NODE")
    assert fail_output["taskStatus"]["state"] == "failed"

    # Check log entries - FAIL_NODE should not add additional entries beyond DELAY_1
    fail_log_entries = parse_agent_log(test_name)
    # Should still have only the DELAY_1 entry, no new entry for FAIL_NODE
    assert len(fail_log_entries) == 1, (
        f"FAIL_NODE should not create log entry, should still have only 1 entry from DELAY_1, got {len(fail_log_entries)}"
    )


def test_hang_command_logging_behavior():
    """Test that HANG command produces no log entry when process is killed."""

    # Get current test name for log file lookup
    test_name = get_current_test_name("test_hang_command_logging_behavior")

    # Clear any existing log entries for this test
    log_file = Path(f"logs/{test_name}.log")
    if log_file.exists():
        log_file.unlink()

    # Start a new mock agent process for HANG test
    hang_proc = subprocess.Popen(
        ["python", "-m", "agentbeacon.mock_agent.stdio_mode"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).parent.parent.parent,
    )

    try:
        # Send HANG command
        hang_proc.stdin.write("HANG\n")
        hang_proc.stdin.flush()

        # Wait 2 seconds then kill process (HANG would normally run for 1 hour)
        time.sleep(2)
        hang_proc.terminate()

        try:
            hang_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            hang_proc.kill()
            hang_proc.wait()

        # Verify HANG didn't create any log entries (process killed before completion)
        hang_log_entries = parse_agent_log(test_name)
        assert len(hang_log_entries) == 0, (
            f"HANG should not create log entry when killed, expected 0 entries, got {len(hang_log_entries)}"
        )

    finally:
        # Ensure process cleanup
        if hang_proc.poll() is None:
            hang_proc.kill()
            try:
                hang_proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


def test_is_special_command_recognizes_send_markdown():
    """SEND_MARKDOWN is recognized as a special command."""
    from agentbeacon.mock_agent.special_commands import SpecialCommands

    sc = SpecialCommands()
    assert sc.is_special_command("SEND_MARKDOWN") is True
    assert sc.is_special_command("send_markdown") is True
    assert sc.is_special_command("  SEND_MARKDOWN  ") is True


def test_is_special_command_recognizes_send_tool_group():
    """SEND_TOOL_GROUP is recognized as a special command."""
    from agentbeacon.mock_agent.special_commands import SpecialCommands

    sc = SpecialCommands()
    assert sc.is_special_command("SEND_TOOL_GROUP") is True
    assert sc.is_special_command("send_tool_group") is True
    assert sc.is_special_command("  SEND_TOOL_GROUP  ") is True
