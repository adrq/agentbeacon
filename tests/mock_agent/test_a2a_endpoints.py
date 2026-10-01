# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Contract tests for A2A JSON-RPC endpoints.

These tests validate the A2A protocol contract compliance by testing:
- SendMessage: Submit messages and return task objects
- GetTask: Retrieve task status by ID
- CancelTask: Cancel running tasks
- HTTP agent card endpoint: /.well-known/agent-card.json

"""

import uuid
import pytest
import httpx
from typing import Dict, Any


@pytest.fixture
def json_rpc_url(mock_agent_a2a):
    return f"{mock_agent_a2a}/rpc"


@pytest.fixture
def valid_message_send_request() -> Dict[str, Any]:
    """Valid SendMessage JSON-RPC request matching A2A v1.0 spec.

    Per A2A v1.0 spec:
    - SendMessage has singular 'message', not 'messages' array
    - contextId is optional field inside Message object, not in params
    - Parts use flat format: {"text": X} instead of {"kind": "text", "text": X}
    - Roles use enum prefix: ROLE_USER, ROLE_AGENT
    """
    return {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "id": 1,
        "params": {
            "message": {
                "messageId": "msg-test-001",
                "role": "ROLE_USER",
                "parts": [{"text": "Hello mock agent"}],
                "contextId": "test-context-123",
            }
        },
    }


def test_message_send_success_response_schema(
    json_rpc_url, valid_message_send_request, assert_canonical_task_contract
):
    response = httpx.post(json_rpc_url, json=valid_message_send_request)

    assert response.status_code == 200
    data = response.json()

    assert data["jsonrpc"] == "2.0"
    assert data["id"] == valid_message_send_request["id"]
    assert "error" not in data, f"Unexpected JSON-RPC error: {data}"
    assert "result" in data

    send_response = data["result"]
    assert "task" in send_response, (
        f"SendMessage result should contain 'task' field: {send_response}"
    )
    task = send_response["task"]

    assert_canonical_task_contract(task)

    assert "id" in task
    assert "contextId" in task
    assert task["contextId"] == "test-context-123"
    assert "history" in task
    assert isinstance(task["history"], list)
    assert task["history"], "history should not be empty"

    first_message = task["history"][0]
    assert first_message["role"] == "ROLE_USER"
    assert isinstance(first_message.get("messageId"), str)
    assert first_message["parts"]
    assert "text" in first_message["parts"][0]

    assert "status" in task
    assert "state" in task["status"]
    assert task["status"]["state"] in [
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]
    assert "artifacts" in task
    assert isinstance(task["artifacts"], list)
    if task["artifacts"]:
        first_artifact = task["artifacts"][0]
        assert "artifactId" in first_artifact
        assert "parts" in first_artifact


def test_message_send_with_special_command_hang(
    json_rpc_url, assert_canonical_task_contract
):
    request = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "id": 2,
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": "HANG"}],
                "contextId": "hang-test",
            }
        },
    }

    response = httpx.post(json_rpc_url, json=request)
    assert response.status_code == 200

    body = response.json()
    assert "error" not in body, f"Unexpected JSON-RPC error: {body}"
    assert "result" in body, f"JSON-RPC response missing 'result': {body}"
    send_response = body["result"]
    assert "task" in send_response, (
        f"SendMessage result should contain 'task': {send_response}"
    )
    task = send_response["task"]
    assert_canonical_task_contract(task)
    assert task["status"]["state"] in ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]


def test_message_send_invalid_request_format(json_rpc_url):
    invalid_request = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "id": 3,
        "params": {},
    }

    response = httpx.post(json_rpc_url, json=invalid_request)
    assert response.status_code == 200

    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32602
    assert "message" in data["error"]


def test_tasks_get_and_cancel_with_errors(
    json_rpc_url, valid_message_send_request, assert_canonical_task_contract
):
    create_response = httpx.post(json_rpc_url, json=valid_message_send_request)
    create_body = create_response.json()
    assert "error" not in create_body, f"Unexpected JSON-RPC error: {create_body}"
    assert "result" in create_body, f"JSON-RPC response missing 'result': {create_body}"
    send_response = create_body["result"]
    assert "task" in send_response, (
        f"SendMessage result should contain 'task': {send_response}"
    )
    created_task = send_response["task"]
    assert_canonical_task_contract(created_task)
    task_id = created_task["id"]

    cancel_request = {
        "jsonrpc": "2.0",
        "method": "CancelTask",
        "id": 2,
        "params": {"id": task_id},
    }

    response = httpx.post(json_rpc_url, json=cancel_request)
    assert response.status_code == 200
    cancel_body = response.json()
    assert "error" not in cancel_body, f"Unexpected JSON-RPC error: {cancel_body}"
    canceled_task = cancel_body["result"]
    assert_canonical_task_contract(canceled_task)
    assert canceled_task["status"]["state"] == "TASK_STATE_CANCELED"

    for method in ["GetTask", "CancelTask"]:
        error_request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": 3,
            "params": {"id": "nonexistent-uuid"},
        }

        response = httpx.post(json_rpc_url, json=error_request)
        assert response.status_code == 200

        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32001
        assert "Task not found" in data["error"]["message"]


def test_agent_card_endpoint_success(mock_agent_a2a):
    response = httpx.get(f"{mock_agent_a2a}/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/json"

    card = response.json()

    assert card["name"] == "Mock A2A Agent"
    assert "version" in card
    assert "capabilities" in card

    assert "supportedInterfaces" in card
    assert isinstance(card["supportedInterfaces"], list)
    assert len(card["supportedInterfaces"]) > 0
    assert "url" in card["supportedInterfaces"][0]


def test_unknown_method_returns_error(json_rpc_url):
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
    assert data["error"]["code"] == -32601
    assert "Method not found" in data["error"]["message"]
