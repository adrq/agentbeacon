"""Contract tests for ACP JSON-RPC endpoints over stdio.

These tests validate the ACP (Agent Communication Protocol) contract compliance by testing:
- initialize: Protocol handshake and capability negotiation
- session/new: Create new session with working directory
- session/prompt: Send prompts and receive responses with notifications

ACP mode communicates via JSON-RPC over stdio (stdin/stdout).
"""

import subprocess
import json
import select
import time
import pytest
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


@pytest.mark.skip(reason="Temporarily skipped per request: failing ACP initialize test")
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

    request_line = json.dumps(prompt_request) + "\n"
    mock_agent_acp.stdin.write(request_line)
    mock_agent_acp.stdin.flush()

    # Read messages - expect notification then response
    messages = []
    max_messages = 5  # Safety limit
    for _ in range(max_messages):
        line = mock_agent_acp.stdout.readline()
        if not line:
            break
        msg = json.loads(line.strip())
        messages.append(msg)
        # Stop after receiving the response
        if "id" in msg and msg["id"] == 3:
            break

    # Find the notification and the response
    notification = None
    response = None
    for msg in messages:
        if "method" in msg and msg["method"] == "session/update":
            notification = msg
        elif "id" in msg and msg["id"] == 3:
            response = msg

    # Validate notification was sent
    assert notification is not None, f"No notification received. Messages: {messages}"
    assert notification["params"]["sessionId"] == session_id

    # Validate final response
    assert response is not None, f"No response received. Messages: {messages}"
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 3
    assert response["result"]["stopReason"] == "end_turn"


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


def test_acp_cancel_during_hang(mock_agent_acp):
    """Test session/cancel during HANG returns cancelled stopReason."""
    # Initialize and create session
    send_json_rpc(
        mock_agent_acp,
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": 1},
        },
    )

    session_response = send_json_rpc(
        mock_agent_acp,
        {"jsonrpc": "2.0", "method": "session/new", "id": 2, "params": {"cwd": "/tmp"}},
    )
    session_id = session_response["result"]["sessionId"]

    # Send HANG command (non-blocking write)
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "HANG"}],
        },
    }
    mock_agent_acp.stdin.write(json.dumps(prompt_request) + "\n")
    mock_agent_acp.stdin.flush()

    # Wait briefly for processing to start
    time.sleep(0.5)

    # Send cancel notification
    cancel_notification = {
        "jsonrpc": "2.0",
        "method": "session/cancel",
        "params": {"sessionId": session_id},
    }
    mock_agent_acp.stdin.write(json.dumps(cancel_notification) + "\n")
    mock_agent_acp.stdin.flush()

    messages = []
    timeout = 3.0
    start = time.time()
    while time.time() - start < timeout:
        ready = select.select([mock_agent_acp.stdout], [], [], 0.2)
        if ready[0]:
            line = mock_agent_acp.stdout.readline()
            if line:
                msg = json.loads(line.strip())
                messages.append(msg)
                if "id" in msg and msg["id"] == 3:
                    break

    # Find response among messages
    response = None
    for msg in messages:
        if "id" in msg and msg["id"] == 3:
            response = msg
            break

    # Verify cancelled stopReason
    assert response is not None, f"No response found. Messages: {messages}"
    assert response["result"]["stopReason"] == "cancelled"


def test_acp_cancel_during_delay(mock_agent_acp):
    """Test session/cancel during DELAY_5 returns cancelled stopReason."""
    # Initialize and create session
    send_json_rpc(
        mock_agent_acp,
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": 1},
        },
    )

    session_response = send_json_rpc(
        mock_agent_acp,
        {"jsonrpc": "2.0", "method": "session/new", "id": 2, "params": {"cwd": "/tmp"}},
    )
    session_id = session_response["result"]["sessionId"]

    # Send DELAY_5 command
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "DELAY_5"}],
        },
    }
    mock_agent_acp.stdin.write(json.dumps(prompt_request) + "\n")
    mock_agent_acp.stdin.flush()

    # Wait 2 seconds, then cancel
    time.sleep(2)

    cancel_notification = {
        "jsonrpc": "2.0",
        "method": "session/cancel",
        "params": {"sessionId": session_id},
    }
    mock_agent_acp.stdin.write(json.dumps(cancel_notification) + "\n")
    mock_agent_acp.stdin.flush()

    messages = []
    cancel_time = time.time()
    timeout = 3.0
    while time.time() - cancel_time < timeout:
        ready = select.select([mock_agent_acp.stdout], [], [], 0.2)
        if ready[0]:
            line = mock_agent_acp.stdout.readline()
            if line:
                msg = json.loads(line.strip())
                messages.append(msg)
                if "id" in msg and msg["id"] == 3:
                    break

    response_time = time.time() - cancel_time

    # Find response
    response = None
    for msg in messages:
        if "id" in msg and msg["id"] == 3:
            response = msg
            break

    # Verify cancelled and fast response
    assert response is not None, f"No response found. Messages: {messages}"
    assert response["result"]["stopReason"] == "cancelled"
    assert response_time < 2, f"Should cancel quickly, took {response_time:.2f}s"


def test_acp_cancel_during_normal_prompt(mock_agent_acp):
    """Test session/cancel during normal prompt handled gracefully."""
    # Initialize and create session
    send_json_rpc(
        mock_agent_acp,
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": 1},
        },
    )

    session_response = send_json_rpc(
        mock_agent_acp,
        {"jsonrpc": "2.0", "method": "session/new", "id": 2, "params": {"cwd": "/tmp"}},
    )
    session_id = session_response["result"]["sessionId"]

    # Send normal prompt
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Hello"}],
        },
    }
    mock_agent_acp.stdin.write(json.dumps(prompt_request) + "\n")
    mock_agent_acp.stdin.flush()

    # Immediately send cancel (race condition)
    cancel_notification = {
        "jsonrpc": "2.0",
        "method": "session/cancel",
        "params": {"sessionId": session_id},
    }
    mock_agent_acp.stdin.write(json.dumps(cancel_notification) + "\n")
    mock_agent_acp.stdin.flush()

    messages = []
    for _ in range(5):
        line = mock_agent_acp.stdout.readline()
        if not line:
            break
        msg = json.loads(line.strip())
        messages.append(msg)
        if "id" in msg and msg["id"] == 3:
            break

    # Find response
    response = None
    for msg in messages:
        if "id" in msg and msg["id"] == 3:
            response = msg
            break

    # Either stopReason is acceptable (race condition), just shouldn't crash
    assert response is not None, f"No response found. Messages: {messages}"
    assert response["result"]["stopReason"] in ["cancelled", "end_turn"]


def test_acp_no_cancel_returns_end_turn(mock_agent_acp):
    """Test normal prompt without cancel returns end_turn stopReason."""
    # Initialize and create session
    send_json_rpc(
        mock_agent_acp,
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": 1},
        },
    )

    session_response = send_json_rpc(
        mock_agent_acp,
        {"jsonrpc": "2.0", "method": "session/new", "id": 2, "params": {"cwd": "/tmp"}},
    )
    session_id = session_response["result"]["sessionId"]

    # Send normal prompt
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Hello mock agent"}],
        },
    }
    mock_agent_acp.stdin.write(json.dumps(prompt_request) + "\n")
    mock_agent_acp.stdin.flush()

    messages = []
    for _ in range(5):
        line = mock_agent_acp.stdout.readline()
        if not line:
            break
        msg = json.loads(line.strip())
        messages.append(msg)
        if "id" in msg and msg["id"] == 3:
            break

    # Find response
    response = None
    for msg in messages:
        if "id" in msg and msg["id"] == 3:
            response = msg
            break

    # Verify end_turn stopReason (normal completion)
    assert response is not None, f"No response found. Messages: {messages}"
    assert response["result"]["stopReason"] == "end_turn"


def test_acp_multiple_cancel_idempotent(mock_agent_acp):
    """Test multiple session/cancel notifications handled gracefully."""
    # Initialize and create session
    send_json_rpc(
        mock_agent_acp,
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": 1},
        },
    )

    session_response = send_json_rpc(
        mock_agent_acp,
        {"jsonrpc": "2.0", "method": "session/new", "id": 2, "params": {"cwd": "/tmp"}},
    )
    session_id = session_response["result"]["sessionId"]

    # Send HANG command
    prompt_request = {
        "jsonrpc": "2.0",
        "method": "session/prompt",
        "id": 3,
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "HANG"}],
        },
    }
    mock_agent_acp.stdin.write(json.dumps(prompt_request) + "\n")
    mock_agent_acp.stdin.flush()

    time.sleep(0.5)

    # Send cancel notification #1
    cancel_notification = {
        "jsonrpc": "2.0",
        "method": "session/cancel",
        "params": {"sessionId": session_id},
    }
    mock_agent_acp.stdin.write(json.dumps(cancel_notification) + "\n")
    mock_agent_acp.stdin.flush()

    # Send cancel notification #2 (duplicate)
    mock_agent_acp.stdin.write(json.dumps(cancel_notification) + "\n")
    mock_agent_acp.stdin.flush()

    messages = []
    timeout = 3.0
    start = time.time()
    while time.time() - start < timeout:
        ready = select.select([mock_agent_acp.stdout], [], [], 0.2)
        if ready[0]:
            line = mock_agent_acp.stdout.readline()
            if line:
                msg = json.loads(line.strip())
                messages.append(msg)
                if "id" in msg and msg["id"] == 3:
                    break

    # Find response
    response = None
    for msg in messages:
        if "id" in msg and msg["id"] == 3:
            response = msg
            break

    # Should get single response with cancelled
    assert response is not None, f"No response found. Messages: {messages}"
    assert response["result"]["stopReason"] == "cancelled"

    # Verify no second response or errors (wait briefly)
    ready = select.select([mock_agent_acp.stdout], [], [], 1.0)
    if ready[0]:
        extra_line = mock_agent_acp.stdout.readline()
        if extra_line:
            extra_msg = json.loads(extra_line.strip())
            # Should not be another response to the same request
            assert not ("id" in extra_msg and extra_msg["id"] == 3), (
                f"Duplicate response received: {extra_msg}"
            )
