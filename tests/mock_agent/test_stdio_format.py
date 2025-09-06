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
    assert "Mock response: DELAY_2" in output["artifacts"][0]["parts"][0]["text"]


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
    assert message["role"] == "assistant"
    assert "content" in message

    content = message["content"]
    assert len(content) == 1
    assert "text" in content[0]
    assert "Mock agent failure: FAIL_NODE" in content[0]["text"]


def test_stdio_json_input_formats(mock_agent_stdio):
    """Test JSON input parsing with various formats and error handling."""
    # Test prompt field
    json_input = json.dumps({"request": {"prompt": "Test prompt"}})
    output = send_stdio_input(mock_agent_stdio, json_input)
    assert output["taskStatus"]["state"] == "completed"
    assert "Mock response: Test prompt" in output["artifacts"][0]["parts"][0]["text"]

    # Test task field
    json_input = json.dumps({"request": {"task": "Test task"}})
    output = send_stdio_input(mock_agent_stdio, json_input)
    assert output["taskStatus"]["state"] == "completed"
    assert "Mock response: Test task" in output["artifacts"][0]["parts"][0]["text"]

    # Test malformed JSON falls back to plain text
    malformed_json = '{"request": {"prompt": "test"'  # Missing closing braces
    output = send_stdio_input(mock_agent_stdio, malformed_json)
    assert output["taskStatus"]["state"] == "completed"
    assert "Mock response:" in output["artifacts"][0]["parts"][0]["text"]
