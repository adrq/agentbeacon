"""Python E2E tests for UI Playwright browser automation.

This test manages the lifecycle of the orchestrator, mock-agent, and triggers
Playwright tests via npm. The Playwright tests run in Firefox headless mode
and test real browser interactions with the AgentBeacon UI.

Testing approach:
- Start mock-agent A2A server on a dynamic port
- Start orchestrator with scheduler + 1 worker (1s polling for fast tests)
- Pass dynamic port to Playwright via SCHEDULER_PORT environment variable
- Invoke npm run test:e2e in web/ directory
- Assert all Playwright tests pass (returncode == 0)
"""

import os
import subprocess
from pathlib import Path

import pytest
from tests.testhelpers import orchestrator_context, start_and_wait_for_a2a_agent

pytestmark = pytest.mark.skip(reason="Deferred: DAG model removed")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_playwright_e2e_suite(test_database):
    """Run Playwright E2E test suite against running orchestrator.

    This test follows the hybrid architecture:
    - Python manages process lifecycle (mock-agent, orchestrator)
    - TypeScript Playwright tests run browser automation
    - Dynamic port allocation via SCHEDULER_PORT env var

    Expected: Playwright tests FAIL (UI components don't exist yet)
    """
    base_dir = Path(__file__).parent.parent

    agent_proc, agent_port = start_and_wait_for_a2a_agent(base_dir=base_dir)

    try:
        with orchestrator_context(
            workers=1, worker_poll_interval="1s", db_url=test_database
        ) as orch:
            scheduler_port = orch["port"]

            env = os.environ.copy()
            env["SCHEDULER_PORT"] = str(scheduler_port)

            result = subprocess.run(
                ["npm", "run", "test:e2e"],
                cwd=str(base_dir / "web"),
                env=env,
                capture_output=True,
                text=True,
            )

            print("=== Playwright Output ===")
            print(result.stdout)
            if result.stderr:
                print("=== Playwright Errors ===")
                print(result.stderr)

            assert result.returncode == 0, (
                f"Playwright tests failed with exit code {result.returncode}"
            )

    finally:
        if agent_proc:
            agent_proc.terminate()
            agent_proc.wait(timeout=5)
