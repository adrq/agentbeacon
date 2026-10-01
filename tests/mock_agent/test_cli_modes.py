# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import subprocess
import time
import json
import httpx
from typing import List

from tests.testhelpers import PortManager, wait_for_port


def start_mock_agent_background(args: List[str]) -> subprocess.Popen:
    cmd = ["uv", "run", "mock-agent"] + args
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )


def test_a2a_mode_starts_correctly():
    port = PortManager().allocate_port()
    a2a_proc = start_mock_agent_background(["--mode", "a2a", "--port", str(port)])
    try:
        assert wait_for_port(
            port, timeout=10, health_path="/.well-known/agent-card.json"
        )
    finally:
        a2a_proc.terminate()
        a2a_proc.wait(timeout=5)


def test_acp_mode_starts_correctly():
    acp_proc = start_mock_agent_background(["--mode", "acp"])
    try:
        time.sleep(1)
        assert acp_proc.poll() is None
    finally:
        acp_proc.terminate()
        acp_proc.wait(timeout=5)


def test_a2a_port_configuration_affects_agent_card():
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
        assert "supportedInterfaces" in card
        assert card["supportedInterfaces"][0]["url"] == f"http://localhost:{port}/rpc"

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_acp_json_rpc_communication():
    proc = start_mock_agent_background(["--mode", "acp"])

    try:
        time.sleep(1)

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

        assert proc.poll() is None

    finally:
        proc.terminate()
        proc.wait(timeout=5)
