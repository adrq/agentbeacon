# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import re
import signal
import subprocess
import time
from pathlib import Path

import requests

from tests.testhelpers import (
    PortManager,
    cleanup_processes,
    start_worker_with_retry_config,
    wait_for_port,
)
from tests.mock_agent_helpers import start_mock_scheduler

BASE_DIR = Path(__file__).parent.parent.parent


def test_worker_respects_polling_interval():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"

        worker_proc = subprocess.Popen(
            [
                "./bin/agentbeacon-worker",
                "--scheduler-url",
                f"http://localhost:{port}",
                "--interval",
                "2s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BASE_DIR,
        )
        processes.append(worker_proc)

        time.sleep(6)

        worker_proc.terminate()
        worker_output, _ = worker_proc.communicate(timeout=5)

        plain_output = re.sub(r"\x1b\[[0-9;]*m", "", worker_output)

        assert "Starting worker loop" in plain_output, (
            f"Worker should log startup: {worker_output}"
        )
        assert "interval=2s" in plain_output, (
            f"Worker should report 2s interval: {worker_output}"
        )

        resp = requests.get(f"http://localhost:{port}/test/sync_log", timeout=5)
        sync_log = resp.json()
        assert len(sync_log) >= 2, (
            f"Expected at least 2 idle syncs in 6s with 2s interval, got {len(sync_log)}"
        )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)


def test_worker_handles_orchestrator_unavailable():
    processes = []

    try:
        worker_proc = start_worker_with_retry_config(
            "http://localhost:19999",
            startup_attempts=3,
            reconnect_attempts=5,
            retry_delay_ms=100,
        )
        processes.append(worker_proc)

        exit_code = worker_proc.wait(timeout=5)
        assert exit_code == 1, (
            f"Worker should exit with code 1 when scheduler unreachable, got {exit_code}"
        )

        worker_output = worker_proc.stdout.read() if worker_proc.stdout else ""

        assert "scheduler unreachable" in worker_output.lower(), (
            f"Worker should mention scheduler unreachable: {worker_output}"
        )
        assert "during startup" in worker_output, (
            f"Worker should indicate startup context: {worker_output}"
        )

        sync_failure_count = worker_output.lower().count("sync failed")
        assert sync_failure_count >= 2, (
            f"Expected at least 2 retry logs, found {sync_failure_count}"
        )
    finally:
        cleanup_processes(processes)


def test_worker_shutdown_on_signal():
    pm = PortManager()
    port = pm.allocate_scheduler_port()
    processes = []

    try:
        scheduler_proc = start_mock_scheduler(port, BASE_DIR)
        processes.append(scheduler_proc)
        assert wait_for_port(port, timeout=10), "Mock scheduler did not start"

        worker_proc = subprocess.Popen(
            [
                "./bin/agentbeacon-worker",
                "--scheduler-url",
                f"http://localhost:{port}",
                "--interval",
                "5s",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BASE_DIR,
        )
        processes.append(worker_proc)

        time.sleep(2)
        assert worker_proc.poll() is None, "Worker should be running"

        worker_proc.send_signal(signal.SIGTERM)

        try:
            exit_code = worker_proc.wait(timeout=10)
            assert exit_code == 0, (
                f"Worker should exit cleanly on SIGTERM, got exit code {exit_code}"
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                "Worker did not shutdown within 10 seconds after SIGTERM"
            )
    finally:
        cleanup_processes(processes)
        pm.release_port(port)
