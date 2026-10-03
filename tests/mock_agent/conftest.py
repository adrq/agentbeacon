# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import uuid
import pytest
import subprocess
import time
import socket
import json
from typing import Dict, Any

from tests.contracts import schema_helpers as contract_schema_helpers


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_server_ready(url: str, timeout: float = 10.0) -> bool:
    import httpx

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = httpx.get(f"{url}/.well-known/agent-card.json", timeout=1)
            if response.status_code == 200:
                return True
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    return False


@pytest.fixture
def mock_agent_a2a():
    port = find_free_port()

    proc = subprocess.Popen(
        ["uv", "run", "mock-agent", "--mode", "a2a", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    server_url = f"http://localhost:{port}"

    _ = wait_for_server_ready(server_url, timeout=5.0)

    yield server_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture
def mock_agent_acp():
    proc = subprocess.Popen(
        ["uv", "run", "mock-agent", "--mode", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    time.sleep(0.2)

    yield proc

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    else:
        proc.wait()


@pytest.fixture(scope="session")
def assert_canonical_task_contract():
    def _assert(payload: Dict[str, Any]) -> None:
        assert "id" in payload, f"task is missing 'id': {list(payload.keys())}"
        assert "status" in payload, f"task is missing 'status': {list(payload.keys())}"
        assert "state" in payload["status"], (
            f"task.status is missing 'state': {payload['status']}"
        )

        assert "history" in payload, "canonical task payload is missing history"

        contract_schema_helpers.validate_payload("a2a-task", payload)

        assert "prompt" not in payload, "unexpected field 'prompt'"
        assert "messages" not in payload, "unexpected field 'messages'"

    return _assert


def send_a2a_message(
    server_url: str,
    message_text: str,
    endpoint: str = "/rpc",
    payload_override: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Helper: Send message via A2A JSON-RPC and return task.

    Uses A2A v1.0 method name (SendMessage) and message format:
    - Parts: {"text": X} (no "kind" field)
    - Role: "ROLE_USER" enum prefix

    For SendMessage (default): unwraps Send Message Response → returns task.
    For GetTask/CancelTask (via payload_override): returns bare Task directly.

    Args:
        server_url: Base URL of the A2A server
        message_text: Text content to send
        endpoint: JSON-RPC endpoint path (default: /rpc)
        payload_override: Optional dict to override default request payload
    """
    import httpx

    default_request = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "id": 1,
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": message_text}],
                "contextId": "test-context",
            }
        },
    }

    request = payload_override if payload_override else default_request

    response = httpx.post(f"{server_url}{endpoint}", json=request)
    body = response.json()
    assert "error" not in body, f"Unexpected JSON-RPC error: {body}"
    assert "result" in body, f"JSON-RPC response missing 'result': {body}"
    result = body["result"]

    if isinstance(result, dict) and "task" in result:
        return result["task"]
    return result


def send_json_rpc(proc: subprocess.Popen, request: Dict[str, Any]) -> Dict[str, Any]:
    request_line = json.dumps(request) + "\n"
    proc.stdin.write(request_line)
    proc.stdin.flush()

    response_line = proc.stdout.readline()
    return json.loads(response_line.strip())
