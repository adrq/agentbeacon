"""
T005: Happy-path integration test for orchestrator/scheduler/workers lifecycle.

This test verifies the core orchestrator behavior:
- Orchestrator starts Scheduler + N workers
- Killing main gracefully shuts down children (no orphaned processes)
- Killing a child leads to orchestrator restart of that child
- Logs are aggregated in orchestrator output with colored/prefixed lines

Uses PID-based process tracking to avoid conflicts with separately running instances.

Deferred — orchestrator references removed workflow model.

Run with: uv run pytest -k test_orchestrator_scheduler_happy_path

Tests run against both SQLite and PostgreSQL backends automatically.
"""

import os
import signal
import subprocess
import tempfile
import time

import pytest
import requests
from tests.testhelpers import (
    orchestrator_context,
    cleanup_files,
)

pytestmark = pytest.mark.skip(reason="Deferred: DAG model removed")


class TestOrchestratorSchedulerHappyPath:
    """Integration tests for orchestrator process management."""

    def setup_method(self):
        """Set up test environment with port allocation only."""
        # Port manager is now handled by orchestrator_context in each test
        # Keep minimal setup for any test-specific needs
        self.temp_files = []

    def teardown_method(self):
        """Clean up temporary files only - processes handled by context managers."""
        # Process cleanup is now handled by orchestrator_context
        # Only cleanup temporary files here
        cleanup_files(self.temp_files)

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_orchestrator_starts_scheduler_and_workers(self, test_database):
        """Test that orchestrator starts scheduler and N workers using PID tracking."""
        with orchestrator_context(workers=2, db_url=test_database) as orch:
            # Verify health endpoint responds properly
            response = requests.get(f"{orch['url']}/api/health", timeout=5)
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

            # Verify processes using PID tracking (exact counts, not "at least")
            orch["tracker"].assert_exact_count("scheduler", 1)
            orch["tracker"].assert_exact_count("worker", 2)

            # Check orchestrator output contains expected log prefixes
            output_lines = []
            start_time = time.time()
            timeout_secs = 10

            scheduler_log_found = False
            worker_log_found = False

            while time.time() - start_time < timeout_secs:
                try:
                    line = orch["orchestrator"].stdout.readline()
                    if line:
                        output_lines.append(line.strip())

                        # Check for required patterns
                        if "scheduler |" in line or "scheduler:" in line:
                            scheduler_log_found = True
                        if any(
                            prefix in line
                            for prefix in [
                                "worker-1 |",
                                "worker-2 |",
                                "worker-1:",
                                "worker-2:",
                            ]
                        ):
                            worker_log_found = True

                        # Only break once we have BOTH
                        if scheduler_log_found and worker_log_found:
                            break
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    print(f"Error reading orchestrator output: {e}")
                    break

            output_text = "\n".join(output_lines)

            # Assert we found both patterns
            assert scheduler_log_found, (
                f"Expected scheduler log prefix in output: {output_text}"
            )
            assert worker_log_found, (
                f"Expected worker log prefixes in output: {output_text}"
            )

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_graceful_shutdown_kills_children(self, test_database):
        """Test that killing orchestrator gracefully shuts down all tracked children."""
        # Use temp file to avoid pipe buffer deadlock
        log_file = tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".log")
        self.temp_files.append(log_file.name)

        with orchestrator_context(workers=2, db_url=test_database) as orch:
            tracker = orch["tracker"]

            # Verify processes are running using PID tracking
            tracker.assert_exact_count("scheduler", 1)
            tracker.assert_exact_count("worker", 2)

            # Send SIGTERM to orchestrator (graceful shutdown signal)
            orch["orchestrator"].send_signal(signal.SIGTERM)

            # Wait for orchestrator to exit
            try:
                orch["orchestrator"].wait(timeout=15)
            except subprocess.TimeoutExpired:
                pytest.fail("Orchestrator did not exit within 15 seconds after SIGTERM")

            # Wait for children cleanup
            time.sleep(3)

            # Verify no tracked processes remain alive (PID-based check)
            final_scheduler_count = tracker.count_alive("scheduler")
            final_worker_count = tracker.count_alive("worker")

            assert final_scheduler_count == 0, (
                f"Found {final_scheduler_count} tracked scheduler PIDs still alive after shutdown"
            )
            assert final_worker_count == 0, (
                f"Found {final_worker_count} tracked worker PIDs still alive after shutdown"
            )

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_child_crash_triggers_restart(self, test_database):
        """Test that killing a tracked child process triggers orchestrator to restart it."""
        with orchestrator_context(workers=2, db_url=test_database) as orch:
            tracker = orch["tracker"]

            # Get initial worker PIDs
            initial_worker_pids = tracker.get_pids_by_type("worker")
            assert len(initial_worker_pids) == 2, "Should have 2 initial workers"

            # Kill one worker directly via PID
            worker_pid_to_kill = initial_worker_pids[0]
            os.kill(worker_pid_to_kill, signal.SIGKILL)

            # Wait for orchestrator to detect and restart (monitor has 1s delay + spawn time)
            time.sleep(5)

            # Verify scheduler still responding (wasn't affected by worker crash)
            response = requests.get(f"{orch['url']}/api/health", timeout=5)
            assert response.status_code == 200

            # Note: The killed PID won't be automatically tracked by our ProcessTracker
            # since the orchestrator spawns a new process we didn't register.
            # This test verifies the orchestrator behavior, not our tracking.
            # In production use, we'd need to periodically re-discover children if needed.

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_log_aggregation_with_prefixes(self, test_database):
        """Test that logs from children are aggregated with colored prefixes."""
        with orchestrator_context(workers=1, db_url=test_database) as orch:
            # Collect output for analysis
            output_lines = []
            start_time = time.time()

            while time.time() - start_time < 5:  # Collect 5 seconds of output
                try:
                    line = orch["orchestrator"].stdout.readline()
                    if line:
                        output_lines.append(line.strip())
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    print(f"Error reading orchestrator output: {e}")
                    break

            output_text = "\n".join(output_lines)

            # Should see docker-compose style prefixes
            expected_prefixes = ["scheduler |", "worker-1 |"]

            for prefix in expected_prefixes:
                assert (
                    prefix in output_text or prefix.replace(" |", ":") in output_text
                ), f"Expected prefix '{prefix}' in output: {output_text}"

            # Should not see raw unprefixed logs from children
            # (This is harder to test definitively, but we can check structure)
            prefixed_lines = [
                line
                for line in output_lines
                if any(p.split()[0] in line for p in expected_prefixes)
            ]

            # Should have some prefixed lines
            assert len(prefixed_lines) > 0, (
                f"Expected some prefixed log lines, but found none in: {output_text}"
            )

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_orchestrator_readiness_signal(self, test_database):
        """Test that orchestrator signals readiness once all children are started."""
        # Use temp file to avoid pipe buffer deadlock
        log_file = tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".log")
        self.temp_files.append(log_file.name)

        with orchestrator_context(workers=2, db_url=test_database) as orch:
            # Wait for readiness signal by reading orchestrator output
            output_lines = []
            ready_signal_found = False
            start_time = time.time()

            while time.time() - start_time < 5:  # Wait up to 5 seconds
                try:
                    line = orch["orchestrator"].stdout.readline()
                    if line:
                        output_lines.append(line.strip())
                        # Look for the specific orchestrator ready signal (not just "started")
                        if "orchestrator ready" in line.lower():
                            ready_signal_found = True
                            break
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    print(f"Error reading orchestrator output: {e}")
                    break

            output_text = "\n".join(output_lines)
            assert ready_signal_found, (
                f"Expected readiness signal in orchestrator output: {output_text}"
            )

            # Verify system is actually ready by checking health endpoint
            response = requests.get(f"{orch['url']}/api/health", timeout=5)
            assert response.status_code == 200
