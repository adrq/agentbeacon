"""Shared pytest fixtures for mock agent testing.

These fixtures handle process management for the mock agent across all test modes:
- stdio: Input/output via stdin/stdout
- A2A: HTTP JSON-RPC server with agent card endpoint
- ACP: JSON-RPC over stdio

Fixtures automatically handle process startup, port allocation, and cleanup.
"""

import uuid
import pytest
import subprocess
import time
import socket
import json
from typing import Dict, Any

from tests.contracts import schema_helpers as contract_schema_helpers


def find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_server_ready(url: str, timeout: float = 10.0) -> bool:
    """Wait for HTTP server to be ready by polling health endpoint."""
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
def mock_agent_stdio():
    """Mock agent in stdio mode for input/output testing."""
    proc = subprocess.Popen(
        ["uv", "run", "mock-agent", "--mode", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    yield proc

    # Cleanup
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture
def mock_agent_a2a():
    """Mock agent in A2A HTTP mode with dynamic port allocation."""
    port = find_free_port()

    proc = subprocess.Popen(
        ["uv", "run", "mock-agent", "--mode", "a2a", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    server_url = f"http://localhost:{port}"

    # Wait for server to be ready
    _ = wait_for_server_ready(server_url, timeout=5.0)

    yield server_url

    # Cleanup
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture
def mock_agent_acp():
    """Mock agent in ACP mode for JSON-RPC over stdio testing."""
    proc = subprocess.Popen(
        ["uv", "run", "mock-agent", "--mode", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # Give process a moment to start and set up stdio pipes before tests begin sending.
    # Without this delay, the first JSON-RPC request may arrive before the stdin handler
    # is ready, causing the request to be lost or the readline() to block indefinitely.
    time.sleep(0.2)

    yield proc

    # Cleanup
    if proc.poll() is None:  # Process still running
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    else:
        # Process already dead, collect exit code
        proc.wait()


def send_stdio_input(proc: subprocess.Popen, input_text: str) -> Dict[str, Any]:
    """Helper: Send input to stdio process and get JSON response."""
    proc.stdin.write(input_text + "\n")
    proc.stdin.flush()
    output_line = proc.stdout.readline()
    return json.loads(output_line.strip())


@pytest.fixture(scope="session")
def assert_canonical_task_contract():
    """Assert that a task payload matches the canonical A2A task contract."""

    def _assert(payload: Dict[str, Any]) -> None:
        canonical_subset = {
            key: payload[key]
            for key in ("history", "artifacts", "contextId", "metadata")
            if key in payload
        }

        # Ensure history exists before attempting validation so failures are explicit.
        assert "history" in canonical_subset, (
            "canonical task payload must include history"
        )

        contract_schema_helpers.validate_payload("a2a-task", canonical_subset)

        # Legacy prompt/messages fields should no longer surface on canonical tasks.
        assert "prompt" not in payload, "legacy prompt field should not appear"
        assert "messages" not in payload, (
            "non-canonical messages field should be removed"
        )

    return _assert


def send_a2a_message(
    server_url: str,
    message_text: str,
    endpoint: str = "/rpc",
    payload_override: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Helper: Send message via A2A JSON-RPC and return task result.

    Args:
        server_url: Base URL of the A2A server
        message_text: Text content to send
        endpoint: JSON-RPC endpoint path (default: /rpc)
        payload_override: Optional dict to override default request payload
    """
    import httpx

    default_request = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": 1,
        "params": {
            "message": {
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
                "contextId": "test-context",
            }
        },
    }

    # Allow payload override for custom testing scenarios
    request = payload_override if payload_override else default_request

    response = httpx.post(f"{server_url}{endpoint}", json=request)
    return response.json()["result"]


def send_json_rpc(proc: subprocess.Popen, request: Dict[str, Any]) -> Dict[str, Any]:
    """Helper: Send JSON-RPC request to ACP process and read response."""
    request_line = json.dumps(request) + "\n"
    proc.stdin.write(request_line)
    proc.stdin.flush()

    response_line = proc.stdout.readline()
    return json.loads(response_line.strip())
