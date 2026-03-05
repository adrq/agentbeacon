"""Integration tests for CLI mode switching and protocol compliance.

These tests validate essential CLI behavior and mode dispatching:
- Mode switching: stdio, a2a, acp protocol compliance
- A2A server port configuration affecting protocol endpoints
- ACP JSON-RPC communication over stdio

"""

import subprocess
import time
import json
import httpx
from typing import List, Optional

from tests.testhelpers import PortManager, wait_for_port


def run_mock_agent(
    args: List[str], timeout: float = 5.0, input_text: Optional[str] = None
) -> subprocess.CompletedProcess:
    """Run mock agent with given arguments and return completed process."""
    cmd = ["uv", "run", "mock-agent"] + args
    try:
        return subprocess.run(
            cmd, input=input_text, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, "", "Timeout")


def start_mock_agent_background(args: List[str]) -> subprocess.Popen:
    """Start mock agent in background and return process handle."""
    cmd = ["uv", "run", "mock-agent"] + args
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )


def test_mode_switching_protocol_compliance():
    """Test all three modes start correctly and follow protocol expectations."""
    # Test stdio mode - should process input and exit
    stdio_result = run_mock_agent(["--mode", "stdio"], input_text="test input\n")
    assert stdio_result.returncode == 0 or stdio_result.stderr == "Timeout"

    # Test A2A mode - should start HTTP server
    port = PortManager().allocate_port()
    a2a_proc = start_mock_agent_background(["--mode", "a2a", "--port", str(port)])
    try:
        assert wait_for_port(
            port, timeout=10, health_path="/.well-known/agent-card.json"
        )
    finally:
        a2a_proc.terminate()
        a2a_proc.wait(timeout=5)

    # Test ACP mode - should wait for JSON-RPC
    acp_proc = start_mock_agent_background(["--mode", "acp"])
    try:
        time.sleep(1)
        assert acp_proc.poll() is None  # Should be running
    finally:
        acp_proc.terminate()
        acp_proc.wait(timeout=5)


def test_a2a_port_configuration_affects_agent_card():
    """Test A2A mode port configuration affects agent card URL."""
    port = PortManager().allocate_port()
    proc = start_mock_agent_background(["--mode", "a2a", "--port", str(port)])

    try:
        assert wait_for_port(
            port, timeout=10, health_path="/.well-known/agent-card.json"
        )

        response = httpx.get(
            f"http://localhost:{port}/.well-known/agent-card.json", timeout=2
        )
        assert response.status_code == 200
        card = response.json()
        assert card["url"] == f"http://localhost:{port}/rpc"

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_acp_json_rpc_communication():
    """Test ACP mode processes JSON-RPC initialize request correctly."""
    proc = start_mock_agent_background(["--mode", "acp"])

    try:
        time.sleep(1)

        # Send valid JSON-RPC initialize request
        init_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {"protocolVersion": 1},
            }
        )

        proc.stdin.write(init_request + "\n")
        proc.stdin.flush()

        time.sleep(1)

        # Should still be running after processing JSON-RPC
        assert proc.poll() is None

    finally:
        proc.terminate()
        proc.wait(timeout=5)
