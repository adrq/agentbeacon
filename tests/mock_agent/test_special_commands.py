# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

import httpx

from .conftest import send_a2a_message


def test_delay_command_a2a_completion(mock_agent_a2a):
    start_time = time.time()
    task = send_a2a_message(mock_agent_a2a, "DELAY_1")
    submission_duration = time.time() - start_time

    assert submission_duration < 1.0
    assert task["status"]["state"] in ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]

    task_id = task["id"]
    poll_start = time.time()

    while time.time() - poll_start < 5.0:
        get_payload = {
            "jsonrpc": "2.0",
            "method": "GetTask",
            "id": 2,
            "params": {"id": task_id},
        }
        updated_task = send_a2a_message(
            mock_agent_a2a, "", payload_override=get_payload
        )

        if updated_task["status"]["state"] == "TASK_STATE_COMPLETED":
            total_duration = time.time() - poll_start
            assert total_duration >= 1.0
            break

        time.sleep(0.1)
    else:
        assert False, f"Task {task_id} did not complete within 5 seconds"


def test_fail_node_a2a_mode(mock_agent_a2a):
    task = send_a2a_message(mock_agent_a2a, "FAIL_NODE")
    task_id = task["id"]

    assert task["status"]["state"] in [
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_FAILED",
    ]

    poll_start = time.time()
    final_task = None

    while time.time() - poll_start < 3.0:
        get_payload = {
            "jsonrpc": "2.0",
            "method": "GetTask",
            "id": 2,
            "params": {"id": task_id},
        }
        final_task = send_a2a_message(mock_agent_a2a, "", payload_override=get_payload)

        if final_task["status"]["state"] in [
            "TASK_STATE_FAILED",
            "TASK_STATE_COMPLETED",
        ]:
            break

        time.sleep(0.1)
    else:
        assert False, f"Task {task_id} did not complete within 3 seconds"

    assert final_task["status"]["state"] == "TASK_STATE_FAILED"
    assert "history" in final_task


def test_hang_command_starts_long_task_a2a(mock_agent_a2a):
    start_time = time.time()
    task = send_a2a_message(mock_agent_a2a, "HANG")
    creation_duration = time.time() - start_time

    assert creation_duration < 1.0

    assert task["status"]["state"] in ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]

    time.sleep(2)
    get_request = {
        "jsonrpc": "2.0",
        "method": "GetTask",
        "id": 2,
        "params": {"id": task["id"]},
    }
    response = httpx.post(f"{mock_agent_a2a}/rpc", json=get_request)
    updated_task = response.json()["result"]

    assert updated_task["status"]["state"] in [
        "TASK_STATE_WORKING",
        "TASK_STATE_SUBMITTED",
    ]


def test_hang_command_can_be_cancelled_a2a(mock_agent_a2a):
    task = send_a2a_message(mock_agent_a2a, "HANG")

    cancel_request = {
        "jsonrpc": "2.0",
        "method": "CancelTask",
        "id": 3,
        "params": {"id": task["id"]},
    }
    response = httpx.post(f"{mock_agent_a2a}/rpc", json=cancel_request)
    cancelled_task = response.json()["result"]

    assert cancelled_task["status"]["state"] == "TASK_STATE_CANCELED"


def test_is_special_command_recognizes_send_markdown():
    from agentbeacon.mock_agent.special_commands import SpecialCommands

    sc = SpecialCommands()
    assert sc.is_special_command("SEND_MARKDOWN") is True
    assert sc.is_special_command("send_markdown") is True
    assert sc.is_special_command("  SEND_MARKDOWN  ") is True


def test_is_special_command_recognizes_send_tool_group():
    from agentbeacon.mock_agent.special_commands import SpecialCommands

    sc = SpecialCommands()
    assert sc.is_special_command("SEND_TOOL_GROUP") is True
    assert sc.is_special_command("send_tool_group") is True
    assert sc.is_special_command("  SEND_TOOL_GROUP  ") is True


def test_is_special_command_recognizes_send_tool_stream():
    from agentbeacon.mock_agent.special_commands import SpecialCommands

    sc = SpecialCommands()
    assert sc.is_special_command("SEND_TOOL_STREAM") is True
    assert sc.is_special_command("send_tool_stream") is True
    assert sc.is_special_command("  SEND_TOOL_STREAM  ") is True


def test_is_special_command_recognizes_send_streaming_markdown():
    from agentbeacon.mock_agent.special_commands import SpecialCommands

    sc = SpecialCommands()
    assert sc.is_special_command("SEND_STREAMING_MARKDOWN") is True
    assert sc.is_special_command("send_streaming_markdown") is True
    assert sc.is_special_command("  SEND_STREAMING_MARKDOWN  ") is True
