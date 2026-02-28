"""Debug test for ACP integration - captures worker logs."""

import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.contracts.schema_helpers import build_acp_task
from tests.testhelpers import (
    cleanup_processes,
    start_mock_scheduler,
    wait_for_port,
    PortManager,
)


@pytest.mark.skip(
    reason="Temporarily skipped per request: failing ACP debug integration test"
)
def test_debug_acp_with_logs():
    """Debug test that captures and prints worker logs."""
    port_manager = PortManager()
    with port_manager.port_context("scheduler") as mock_orchestrator_port:
        processes = []

        try:
            scheduler_proc = start_mock_scheduler(
                mock_orchestrator_port, Path(__file__).parent.parent.parent
            )
            processes.append(scheduler_proc)

            scheduler_ready = wait_for_port(mock_orchestrator_port, timeout=10)
            assert scheduler_ready, "Mock scheduler should start"

            # Add a simple ACP task
            acp_task = build_acp_task(
                node_id="debug-node",
                text="Hello",
                cwd="/tmp",
                agent="test-acp-agent",
                execution_id="debug-exec",
            )

            response = requests.post(
                f"http://localhost:{mock_orchestrator_port}/add_task", json=acp_task
            )
            assert response.status_code == 200
            print(f"Task added: {response.json()}")

            # Start worker with debug logging and capture output
            base_dir = Path(__file__).parent.parent.parent
            worker_cmd = [
                "./bin/agentbeacon-worker",
                "--scheduler-url",
                f"http://localhost:{mock_orchestrator_port}",
                "--interval",
                "1s",
            ]

            # Set RUST_LOG to get detailed logs from worker
            import os

            worker_env = os.environ.copy()
            worker_env["RUST_LOG"] = "agentbeacon_worker=debug,worker=debug"

            worker_proc = subprocess.Popen(
                worker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=base_dir,
                env=worker_env,
            )
            processes.append(worker_proc)
            print("Worker started with debug logging")

            # Wait for task to complete
            time.sleep(5)

            # Terminate worker and capture output
            worker_proc.terminate()
            stdout, _ = worker_proc.communicate(timeout=5)

            print("\n===== WORKER LOGS =====")
            print(stdout)
            print("===== END WORKER LOGS =====\n")

            # Check results
            result = requests.get(f"http://localhost:{mock_orchestrator_port}/results")
            assert result.status_code == 200
            results = result.json()
            print(f"\nResults: {results}")

            task_result = [r for r in results if r["executionId"] == "debug-exec"]
            print(f"\nFiltered results: {task_result}")

            if len(task_result) == 0:
                print("\nERROR: No results found!")
                print("Worker should have completed the task but didn't.")
            elif len(task_result) == 1:
                print(
                    f"\nSUCCESS: Task completed with state: {task_result[0]['taskStatus']['state']}"
                )
            else:
                print(f"\nUNEXPECTED: Multiple results found: {len(task_result)}")

        finally:
            cleanup_processes(processes)
