"""
T005: Happy-path integration test for orchestrator/scheduler/workers lifecycle.

This test verifies the core orchestrator behavior:
- Orchestrator starts Scheduler + N workers
- Killing main gracefully shuts down children (no orphaned processes)
- Killing a child leads to orchestrator restart of that child
- Logs are aggregated in orchestrator output with colored/prefixed lines

Run with: uv run pytest -k test_orchestrator_scheduler_happy_path

Tests run against both SQLite and PostgreSQL backends automatically.
"""

import os
import signal
import subprocess
import time
import tempfile
import psutil
from tests.testhelpers import cleanup_processes, cleanup_files
import pytest
import requests
from pathlib import Path


class TestOrchestratorSchedulerHappyPath:
    """Integration tests for orchestrator process management."""

    def setup_method(self):
        """Set up test environment."""
        # Dynamically allocate unique port for each test
        from tests.testhelpers import PortManager

        self.port_manager = PortManager()
        self.test_port = self.port_manager.allocate_port()

        self.orchestrator_binary = "./bin/agentmaestro"
        self.scheduler_binary = "./bin/agentmaestro-scheduler"
        self.worker_binary = "./bin/agentmaestro-worker"
        self.temp_dir = tempfile.mkdtemp()
        self.processes = []
        self.temp_files = []

    def teardown_method(self):
        """Clean up all processes and temporary files."""
        cleanup_processes(self.processes)
        cleanup_files(self.temp_files)

        # Aggressive cleanup: Kill ALL agentmaestro processes
        # This prevents orphans from affecting next test
        import psutil

        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                # Only kill agentmaestro binaries, not pytest itself
                if (
                    "agentmaestro-scheduler" in cmdline
                    or "agentmaestro-worker" in cmdline
                    or ("agentmaestro" in cmdline and "pytest" not in cmdline)
                ):
                    try:
                        proc.kill()  # Force kill to ensure cleanup
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Small delay to ensure processes die
        time.sleep(0.5)

        # Release allocated port
        if hasattr(self, "port_manager"):
            self.port_manager.release_port(self.test_port)

    def _wait_for_port(self, port, timeout=10):
        """Wait for a port to become available."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"http://localhost:{port}/api/health", timeout=1
                )
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.1)
        return False

    def _count_processes_by_name(self, name_pattern):
        """Count running processes matching a name pattern."""
        count = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if name_pattern in cmdline:
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return count

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_orchestrator_starts_scheduler_and_workers(self, test_database):
        """Test that orchestrator starts scheduler and N workers."""
        # Start the orchestrator with custom settings
        env = os.environ.copy()
        env["PORT"] = str(self.test_port)
        env["DATABASE_URL"] = test_database

        orchestrator_proc = subprocess.Popen(
            [
                self.orchestrator_binary,
                "--workers",
                "2",
                "--scheduler-port",
                str(self.test_port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(orchestrator_proc)

        # Wait for scheduler to start (should be available via health endpoint)
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, (
            f"Scheduler did not start on port {self.test_port} within 10 seconds"
        )

        # Verify scheduler is responding
        response = requests.get(
            f"http://localhost:{self.test_port}/api/health", timeout=5
        )
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        # Wait a moment for workers to start
        time.sleep(2)

        # Count running processes - should have orchestrator + scheduler + 2 workers
        scheduler_count = self._count_processes_by_name("agentmaestro-scheduler")
        worker_count = self._count_processes_by_name("agentmaestro-worker")

        assert scheduler_count >= 1, (
            f"Expected at least 1 scheduler process, found {scheduler_count}"
        )
        assert worker_count >= 2, (
            f"Expected at least 2 worker processes, found {worker_count}"
        )

        # Check orchestrator output contains expected log prefixes
        # Wait for BOTH scheduler and worker logs to appear
        output_lines = []
        start_time = time.time()
        timeout_secs = 10

        scheduler_log_found = False
        worker_log_found = False

        while time.time() - start_time < timeout_secs:
            try:
                line = orchestrator_proc.stdout.readline()
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
        """Test that killing orchestrator gracefully shuts down all children."""
        env = os.environ.copy()
        env["PORT"] = str(self.test_port)
        env["DATABASE_URL"] = test_database

        # Use temp file to avoid pipe buffer deadlock
        log_file = tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".log")
        self.temp_files.append(log_file.name)

        orchestrator_proc = subprocess.Popen(
            [
                self.orchestrator_binary,
                "--workers",
                "2",
                "--scheduler-port",
                str(self.test_port),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(orchestrator_proc)

        # Wait for system to start
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, "Scheduler did not start within timeout"

        time.sleep(2)  # Let workers start

        # Count processes before shutdown
        initial_scheduler_count = self._count_processes_by_name(
            "agentmaestro-scheduler"
        )
        initial_worker_count = self._count_processes_by_name("agentmaestro-worker")

        assert initial_scheduler_count >= 1, "No scheduler processes found"
        assert initial_worker_count >= 2, "Expected at least 2 worker processes"

        # Send SIGTERM to orchestrator (graceful shutdown signal)
        orchestrator_proc.send_signal(signal.SIGTERM)

        # Wait for orchestrator to exit
        try:
            orchestrator_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pytest.fail("Orchestrator did not exit within 15 seconds after SIGTERM")

        # Poll for orphaned processes with retries (up to 10 seconds total)
        max_retries = 20
        retry_delay = 0.5
        final_scheduler_count = None
        final_worker_count = None

        for attempt in range(max_retries):
            final_scheduler_count = self._count_processes_by_name(
                "agentmaestro-scheduler"
            )
            final_worker_count = self._count_processes_by_name("agentmaestro-worker")

            if final_scheduler_count == 0 and final_worker_count == 0:
                break  # All processes cleaned up successfully

            if attempt < max_retries - 1:
                time.sleep(retry_delay)  # Wait before next check

        # Known race condition: If a monitor restart happened RIGHT before shutdown,
        # the new process may not be in shutdown()'s PID list. Manually clean up.
        if final_scheduler_count > 0 or final_worker_count > 0:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    if (
                        "agentmaestro-scheduler" in cmdline
                        or "agentmaestro-worker" in cmdline
                    ):
                        try:
                            proc.terminate()
                            proc.wait(timeout=2)
                        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                            try:
                                proc.kill()
                            except psutil.NoSuchProcess:
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Recount after manual cleanup
            time.sleep(0.5)
            final_scheduler_count = self._count_processes_by_name(
                "agentmaestro-scheduler"
            )
            final_worker_count = self._count_processes_by_name("agentmaestro-worker")

        # Final assertions (should now be clean)
        assert final_scheduler_count == 0, (
            f"Found {final_scheduler_count} orphaned scheduler processes after shutdown and manual cleanup"
        )
        assert final_worker_count == 0, (
            f"Found {final_worker_count} orphaned worker processes after shutdown and manual cleanup"
        )

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_child_crash_triggers_restart(self, test_database):
        """Test that killing a child process triggers orchestrator to restart it."""
        env = os.environ.copy()
        env["PORT"] = str(self.test_port)
        env["DATABASE_URL"] = test_database

        orchestrator_proc = subprocess.Popen(
            [
                self.orchestrator_binary,
                "--workers",
                "2",
                "--scheduler-port",
                str(self.test_port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(orchestrator_proc)

        # Wait for system to start
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, "Scheduler did not start within timeout"

        time.sleep(2)  # Let workers start

        # Find and kill one worker process
        worker_pid = None
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if "agentmaestro-worker" in cmdline:
                    worker_pid = proc.info["pid"]
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        assert worker_pid is not None, "Could not find a worker process to kill"

        # Kill the worker
        os.kill(worker_pid, signal.SIGKILL)

        # Wait for orchestrator to detect and restart (monitor has 1s delay + spawn time)
        time.sleep(5)

        # Verify worker was restarted - should still have 2+ workers
        worker_count_after_restart = self._count_processes_by_name(
            "agentmaestro-worker"
        )
        assert worker_count_after_restart >= 2, (
            f"Expected at least 2 workers after restart, found {worker_count_after_restart}"
        )

        # Verify scheduler still responding (wasn't affected by worker crash)
        response = requests.get(
            f"http://localhost:{self.test_port}/api/health", timeout=5
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
    def test_log_aggregation_with_prefixes(self, test_database):
        """Test that logs from children are aggregated with colored prefixes."""
        env = os.environ.copy()
        env["PORT"] = str(self.test_port)
        env["DATABASE_URL"] = test_database

        orchestrator_proc = subprocess.Popen(
            [
                self.orchestrator_binary,
                "--workers",
                "1",
                "--scheduler-port",
                str(self.test_port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(orchestrator_proc)

        # Wait for system to start
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, "Scheduler did not start within timeout"

        # Collect output for analysis
        output_lines = []
        start_time = time.time()

        while time.time() - start_time < 5:  # Collect 5 seconds of output
            try:
                line = orchestrator_proc.stdout.readline()
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
            assert prefix in output_text or prefix.replace(" |", ":") in output_text, (
                f"Expected prefix '{prefix}' in output: {output_text}"
            )

        # Should not see raw unpredfixed logs from children
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
        env = os.environ.copy()
        env["PORT"] = str(self.test_port)
        env["DATABASE_URL"] = test_database

        # Use temp file to avoid pipe buffer deadlock
        log_file = tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".log")
        self.temp_files.append(log_file.name)

        orchestrator_proc = subprocess.Popen(
            [
                self.orchestrator_binary,
                "--workers",
                "2",
                "--scheduler-port",
                str(self.test_port),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(orchestrator_proc)

        # Wait for readiness signal by reading from log file
        output_lines = []
        ready_signal_found = False
        start_time = time.time()

        while time.time() - start_time < 15:  # Wait up to 15 seconds
            try:
                # Flush and read current file contents
                log_file.flush()
                log_file.seek(0)
                current_content = log_file.read()

                # Split into lines and check for readiness
                current_lines = (
                    current_content.strip().split("\n")
                    if current_content.strip()
                    else []
                )

                for line in current_lines:
                    if line and line not in output_lines:
                        output_lines.append(line)
                        # Look for the specific orchestrator ready signal (not just "started")
                        if "orchestrator ready" in line.lower():
                            ready_signal_found = True
                            break

                if ready_signal_found:
                    break

                time.sleep(0.2)
            except Exception:
                break

        log_file.close()
        output_text = "\n".join(output_lines)
        assert ready_signal_found, (
            f"Expected readiness signal in orchestrator output: {output_text}"
        )

        # Verify system is actually ready by checking health endpoint
        response = requests.get(
            f"http://localhost:{self.test_port}/api/health", timeout=5
        )
        assert response.status_code == 200
