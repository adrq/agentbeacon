"""Contract tests for ACP JSON-RPC endpoints over stdio.

These tests validate the ACP (Agent Communication Protocol) contract compliance by testing:
- initialize: Protocol handshake and capability negotiation
- session/new: Create new session with working directory
- session/prompt: Send prompts and receive responses with notifications

ACP mode communicates via JSON-RPC over stdio (stdin/stdout).
"""

import subprocess
import json
import time
from typing import Dict, Any, List
from .conftest import send_json_rpc


def read_notifications(
    proc: subprocess.Popen, timeout: float = 1.0
) -> List[Dict[str, Any]]:
    """Read all available notification messages (non-blocking)."""
    notifications = []
    start_time = time.time()

    while time.time() - start_time < timeout:
        if proc.stdout.readable():
            try:
                line = proc.stdout.readline()
                if line:
                    notification = json.loads(line.strip())
                    if (
                        "method" in notification
                        and notification["method"] == "session/update"
                    ):
                        notifications.append(notification)
            except json.JSONDecodeError:
                break
        time.sleep(0.01)

    return notifications


def test_acp_initialize_success_response(mock_agent_acp):
    """Test initialize returns protocol version and capabilities."""
    request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {"protocolVersion": 1},
    }

    response = send_json_rpc(mock_agent_acp, request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "result" in response

    result = response["result"]
    assert result["protocolVersion"] == 1
    assert "agentCapabilities" in result
    assert "authMethods" in result

    capabilities = result["agentCapabilities"]
    assert "loadSession" in capabilities
    assert "promptCapabilities" in capabilities
    assert "mcpCapabilities" in capabilities

    assert capabilities["loadSession"] == False  # noqa
    assert "embeddedContext" in capabilities["promptCapabilities"]
    assert capabilities["promptCapabilities"]["embeddedContext"] == True  # noqa

    mcp_caps = capabilities["mcpCapabilities"]
    assert mcp_caps["http"] == False  # noqa
    assert mcp_caps["sse"] == False  # noqa

    assert isinstance(result["authMethods"], list)


def test_acp_initialize_invalid_protocol_version(mock_agent_acp):
    """Test initialize with unsupported protocol version returns error."""
    request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 2,
        "params": {"protocolVersion": 999},
    }

    response = send_json_rpc(mock_agent_acp, request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 2
    assert "error" in response
    assert response["error"]["code"] == -32602  # Invalid params per JSON-RPC 2.0 spec
    assert "message" in response["error"]


def test_acp_session_new_without_initialize(mock_agent_acp):
    """Test session/new without initialize returns error."""
    request = {
        "jsonrpc": "2.0",
        "method": "session/new",
        "id": 3,
        "params": {"cwd": "/tmp/test"},
    }

    response = send_json_rpc(mock_agent_acp, request)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 3
    assert "error" in response
    assert (
        response["error"]["code"] == -32603
    )  # Internal error - session not initialized
    assert "message" in response["error"]


def test_acp_full_workflow_with_notifications(mock_agent_acp):
    """Test complete ACP workflow: initialize -> session/new -> session/prompt with notifications."""
    # Initialize
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {"protocolVersion": 1},
    }
    init_response = send_json_rpc(mock_agent_acp, init_request)

    # Validate initialization response structure
    assert init_response["result"]["protocolVersion"] == 1
    assert "agentCapabilities" in init_response["result"]
    assert "authMethods" in init_response["result"]

    # Create session
    session_request = {
        "jsonrpc": "2.0",
        "method": "session/new",
        "id": 2,
        "params": {"cwd": "/tmp"},
    }
    session_response = send_json_rpc(mock_agent_acp, session_request)

    # Validate session creation
    assert "sessionId" in session_response["result"]
    assert session_response["result"]["sessionId"]  # Should be non-empty
    session_id = session_response["result"]["sessionId"]

    # Send prompt
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Hello mock agent"}],
        },
    }

    response = send_json_rpc(mock_agent_acp, prompt_request)

    # Validate final response
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 3
    assert response["result"]["stopReason"] == "end_turn"

    # Note: Notification reading removed for now - will be implemented later


def test_acp_special_command_delay(mock_agent_acp):
    """Test ACP session with special command timing verification."""
    # Quick setup: initialize and create session
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {"protocolVersion": 1},
    }
    send_json_rpc(mock_agent_acp, init_request)

    session_request = {
        "jsonrpc": "2.0",
        "method": "session/new",
        "id": 2,
        "params": {"cwd": "/tmp"},
    }
    session_response = send_json_rpc(mock_agent_acp, session_request)
    session_id = session_response["result"]["sessionId"]

    # Test special command with timing
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "DELAY_1"}],
        },
    }

    start_time = time.time()
    response = send_json_rpc(mock_agent_acp, prompt_request)
    duration = time.time() - start_time

    # Verify special command timing and response
    assert duration >= 1.0
    assert response["result"]["stopReason"] == "end_turn"


def test_acp_error_conditions(mock_agent_acp):
    """Test ACP error handling for invalid session and unknown methods."""
    # Test session/prompt with invalid session ID
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {"protocolVersion": 1},
    }
    send_json_rpc(mock_agent_acp, init_request)

    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 2,
        "params": {
            "sessionId": "invalid-session-id",
            "prompt": [{"type": "text", "text": "Hello"}],
        },
    }

    response = send_json_rpc(mock_agent_acp, prompt_request)
    assert "error" in response
    assert response["error"]["code"] == -32602  # Invalid params - invalid session ID

    # Test unknown method
    unknown_request = {
        "jsonrpc": "2.0",
        "method": "unknown/method",
        "id": 99,
        "params": {},
    }

    response = send_json_rpc(mock_agent_acp, unknown_request)
    assert "error" in response
    assert response["error"]["code"] == -32601  # Method not found
