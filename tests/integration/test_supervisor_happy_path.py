# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import signal
import subprocess
import time

import psutil
import pytest
import requests
from tests.testhelpers import (
    orchestrator_context,
)


def test_standalone_scheduler_no_workers():
    with orchestrator_context(max_workers=0) as orch:
        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        assert orch["orchestrator"].poll() is None, (
            "agentbeacon process exited unexpectedly"
        )

        try:
            parent = psutil.Process(orch["orchestrator_pid"])
            children = parent.children(recursive=False)
            worker_children = [
                c
                for c in children
                if "agentbeacon-worker" in " ".join(c.cmdline() or [])
            ]
            assert len(worker_children) == 0, (
                f"Expected 0 worker children, found {len(worker_children)}"
            )
        except psutil.NoSuchProcess:
            pass


def test_max_workers_zero_no_spawn():
    with orchestrator_context(max_workers=0) as orch:
        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200

        assert orch["orchestrator"].poll() is None, (
            "agentbeacon process exited unexpectedly"
        )

        try:
            parent = psutil.Process(orch["orchestrator_pid"])
            children = parent.children(recursive=False)
            worker_children = [
                c
                for c in children
                if "agentbeacon-worker" in " ".join(c.cmdline() or [])
            ]
            assert len(worker_children) == 0
        except psutil.NoSuchProcess:
            pass


def test_graceful_shutdown():
    with orchestrator_context(max_workers=0) as orch:
        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200

        orch["orchestrator"].send_signal(signal.SIGTERM)

        try:
            orch["orchestrator"].wait(timeout=15)
        except subprocess.TimeoutExpired:
            pytest.fail("agentbeacon did not exit within 15 seconds after SIGTERM")


def test_readiness_message():
    with orchestrator_context(max_workers=0) as orch:
        output_lines = []
        ready_found = False
        deadline = time.time() + 15

        with open(orch["orchestrator_log"], errors="replace") as log:
            while time.time() < deadline:
                line = log.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                output_lines.append(line.strip())
                if "agentbeacon ready" in line.lower():
                    ready_found = True
                    break

        assert ready_found, (
            f"Expected 'AgentBeacon ready' in output: {output_lines[:20]}"
        )

        response = requests.get(f"{orch['url']}/api/health", timeout=5)
        assert response.status_code == 200
