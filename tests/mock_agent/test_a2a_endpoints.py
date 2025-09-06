"""Contract tests for A2A JSON-RPC endpoints.

These tests validate the A2A protocol contract compliance by testing:
- message/send: Submit messages and return task objects
- tasks/get: Retrieve task status by ID
- tasks/cancel: Cancel running tasks
- HTTP agent card endpoint: /.well-known/agent-card.json

"""

import pytest
import httpx
from typing import Dict, Any


@pytest.fixture
def json_rpc_url(mock_agent_a2a):
    """JSON-RPC endpoint URL."""
    return f"{mock_agent_a2a}/rpc"


@pytest.fixture
def valid_message_send_request() -> Dict[str, Any]:
    """Valid message/send JSON-RPC request matching contract."""
    return {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": 1,
        "params": {
            "contextId": "test-context-123",
            "messages": [
                {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Hello mock agent"}],
                }
            ],
        },
    }


def test_message_send_success_response_schema(json_rpc_url, valid_message_send_request):
    """Test message/send returns valid task object with required fields."""
    response = httpx.post(json_rpc_url, json=valid_message_send_request)

    assert response.status_code == 200
    data = response.json()

    # Validate JSON-RPC response structure
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == valid_message_send_request["id"]
    assert "result" in data

    task = data["result"]

    # Validate task object schema per contract
    assert "id" in task
    assert "contextId" in task
    assert task["contextId"] == "test-context-123"
    assert "status" in task
    assert "state" in task["status"]
    assert task["status"]["state"] in ["submitted"]
    assert "history" in task
    assert "artifacts" in task
    assert isinstance(task["history"], list)
    assert isinstance(task["artifacts"], list)


def test_message_send_with_special_command_hang(json_rpc_url):
    """Test message/send with HANG command triggers long-running task."""
    request = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": 2,
        "params": {
            "contextId": "hang-test",
            "messages": [{"role": "user", "parts": [{"kind": "text", "text": "HANG"}]}],
        },
    }

    response = httpx.post(json_rpc_url, json=request)
    assert response.status_code == 200

    task = response.json()["result"]
    assert task["status"]["state"] in ["submitted", "working"]


def test_message_send_invalid_request_format(json_rpc_url):
    """Test message/send with invalid request returns JSON-RPC error."""
    invalid_request = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": 3,
        "params": {},  # Missing required fields
    }

    response = httpx.post(json_rpc_url, json=invalid_request)
    assert response.status_code == 200  # JSON-RPC errors return 200 with error object

    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32602  # Invalid params per JSON-RPC 2.0 spec
    assert "message" in data["error"]


def test_tasks_get_and_cancel_with_errors(json_rpc_url, valid_message_send_request):
    """Test task retrieval, cancellation, and error handling for nonexistent tasks."""
    # Test task cancellation with valid task
    create_response = httpx.post(json_rpc_url, json=valid_message_send_request)
    task_id = create_response.json()["result"]["id"]

    cancel_request = {
        "jsonrpc": "2.0",
        "method": "tasks/cancel",
        "id": 2,
        "params": {"taskId": task_id},
    }

    response = httpx.post(json_rpc_url, json=cancel_request)
    assert response.status_code == 200
    assert response.json()["result"]["status"]["state"] == "canceled"

    # Test TaskNotFoundError for both get and cancel
    for method in ["tasks/get", "tasks/cancel"]:
        error_request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": 3,
            "params": {"taskId": "nonexistent-uuid"},
        }

        response = httpx.post(json_rpc_url, json=error_request)
        assert response.status_code == 200

        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32001  # TaskNotFoundError per A2A spec
        assert "Task not found" in data["error"]["message"]


def test_agent_card_endpoint_success(mock_agent_a2a):
    """Test agent card endpoint returns valid A2A agent card with proper capabilities."""
    response = httpx.get(f"{mock_agent_a2a}/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/json"

    card = response.json()

    # Validate agent card schema per A2A v0.3.0 contract
    assert card["protocolVersion"] == "0.3.0"
    assert card["name"] == "Mock A2A Agent"
    assert card["preferredTransport"] == "JSONRPC"
    assert "url" in card
    assert "version" in card
    assert "capabilities" in card
    assert "defaultInputModes" in card
    assert "defaultOutputModes" in card

    # Validate mock agent capabilities
    capabilities = card["capabilities"]
    assert capabilities["streaming"] == False  # noqa
    assert capabilities["pushNotifications"] == False  # noqa


def test_unknown_method_returns_error(json_rpc_url):
    """Test unknown JSON-RPC method returns method not found error."""
    unknown_request = {
        "jsonrpc": "2.0",
        "method": "unknown/method",
        "id": 99,
        "params": {},
    }

    response = httpx.post(json_rpc_url, json=unknown_request)
    assert response.status_code == 200

    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32601  # Method not found per JSON-RPC 2.0 spec
    assert "Method not found" in data["error"]["message"]
