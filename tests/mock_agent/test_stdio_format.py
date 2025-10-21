"""Contract tests for stdio I/O format.

These tests validate the stdio mode contract compliance by testing:
- Plain text input → A2A-compliant JSON output
- JSON input with request wrapper → A2A-compliant JSON output
- Success format with taskStatus and artifacts
- Failure format with error messages
- Special command handling (HANG, DELAY, FAIL)

stdio mode communicates via stdin/stdout with line-oriented I/O.
"""

import json
import time
from .conftest import send_stdio_input


def test_stdio_plain_text_success_response(mock_agent_stdio):
    """Test plain text input returns A2A-compliant success JSON."""
    output = send_stdio_input(mock_agent_stdio, "Hello mock agent")

    # Validate success response structure per contract
    assert "taskStatus" in output
    assert "artifacts" in output

    task_status = output["taskStatus"]
    assert task_status["state"] == "completed"
    assert "timestamp" in task_status
    if "message" in task_status and task_status["message"] is not None:
        status_message = task_status["message"]
        assert status_message["kind"] == "message"
        assert isinstance(status_message.get("messageId"), str)
        assert status_message["parts"][0]["kind"] == "text"

    artifacts = output["artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) == 1

    artifact = artifacts[0]
    assert "artifactId" in artifact
    assert artifact["name"] == "agent-output"
    assert artifact["description"] == "Output from mock agent execution"
    assert "parts" in artifact

    parts = artifact["parts"]
    assert len(parts) == 1
    assert parts[0]["kind"] == "text"
    assert "text" in parts[0]
    assert "Mock response: Hello mock agent" in parts[0]["text"]


def test_stdio_plain_text_with_delay_command(mock_agent_stdio):
    """Test plain text with DELAY_2 command takes appropriate time."""
    start_time = time.time()
    output = send_stdio_input(mock_agent_stdio, "DELAY_2")
    duration = time.time() - start_time

    # Should take at least 2 seconds
    assert duration >= 2.0

    # Should still return success format
    assert output["taskStatus"]["state"] == "completed"
    parts = output["artifacts"][0]["parts"]
    assert parts[0]["kind"] == "text"
    assert "Mock response: DELAY_2" in parts[0]["text"]


def test_stdio_plain_text_with_fail_command(mock_agent_stdio):
    """Test plain text with FAIL_NODE command returns failure format."""
    output = send_stdio_input(mock_agent_stdio, "FAIL_NODE")

    # Validate failure response structure per contract
    assert "taskStatus" in output
    assert "artifacts" not in output  # Failure format doesn't include artifacts

    task_status = output["taskStatus"]
    assert task_status["state"] == "failed"
    assert "timestamp" in task_status
    assert "message" in task_status

    message = task_status["message"]
    assert message["kind"] == "message"
    assert message["role"] == "assistant"
    assert isinstance(message.get("messageId"), str)
    assert message["parts"], "failure message should include parts"
    assert message["parts"][0]["kind"] == "text"
    assert "text" in message["parts"][0]
    assert "Mock agent failure: FAIL_NODE" in message["parts"][0]["text"]


def test_stdio_json_input_formats(mock_agent_stdio):
    """Test JSON input parsing with various formats and error handling."""
    canonical_task = {
        "message": {
            "kind": "message",
            "messageId": "stdin-001",
            "role": "user",
            "parts": [{"kind": "text", "text": "Test prompt"}],
        },
        "configuration": {},
        "metadata": {"priority": "normal"},
    }

    json_input = json.dumps({"request": {"task": canonical_task}})
    output = send_stdio_input(mock_agent_stdio, json_input)
    assert output["taskStatus"]["state"] == "completed"
    assert output["artifacts"][0]["parts"][0]["kind"] == "text"
    assert "Mock response: Test prompt" in output["artifacts"][0]["parts"][0]["text"]
    assert "prompt" not in output, (
        "legacy prompt field should not appear in stdio response"
    )

    # Test malformed JSON falls back to plain text
    malformed_json = '{"request": {"prompt": "test"'  # Missing closing braces
    output = send_stdio_input(mock_agent_stdio, malformed_json)
    assert output["taskStatus"]["state"] == "completed"
    assert "Mock response:" in output["artifacts"][0]["parts"][0]["text"]
    assert output["artifacts"][0]["parts"][0]["kind"] == "text"
