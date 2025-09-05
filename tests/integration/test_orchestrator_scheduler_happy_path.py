"""
T005: Happy-path integration test for orchestrator/scheduler/workers lifecycle.

This test verifies the core orchestrator behavior:
- Orchestrator starts Scheduler + N workers
- Killing main gracefully shuts down children (no orphaned processes)
- Killing a child leads to orchestrator restart of that child
- Logs are aggregated in orchestrator output with colored/prefixed lines

Run with: uv run pytest -k test_orchestrator_scheduler_happy_path
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
        # Use a unique port for each test to avoid conflicts
        self.test_port = 19456  # Different from default 9456
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

    def _wait_for_port(self, port, timeout=10):
        """Wait for a port to become available."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"http://localhost:{port}/api/health", timeout=1)
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.1)
        return False

    def _count_processes_by_name(self, name_pattern):
        """Count running processes matching a name pattern."""
        count = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if name_pattern in cmdline:
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return count

    def test_orchestrator_starts_scheduler_and_workers(self):
        """Test that orchestrator starts scheduler and N workers."""
        # This test will fail until the orchestrator is implemented

        # Start the orchestrator with custom settings
        env = os.environ.copy()
        env['PORT'] = str(self.test_port)

        orchestrator_proc = subprocess.Popen(
            [self.orchestrator_binary, "-workers", "2", "-scheduler-port", str(self.test_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent
        )
        self.processes.append(orchestrator_proc)

        # Wait for scheduler to start (should be available via health endpoint)
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, f"Scheduler did not start on port {self.test_port} within 10 seconds"

        # Verify scheduler is responding
        response = requests.get(f"http://localhost:{self.test_port}/api/health", timeout=5)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Wait a moment for workers to start
        time.sleep(2)

        # Count running processes - should have orchestrator + scheduler + 2 workers
        scheduler_count = self._count_processes_by_name("agentmaestro-scheduler")
        worker_count = self._count_processes_by_name("agentmaestro-worker")

        assert scheduler_count >= 1, f"Expected at least 1 scheduler process, found {scheduler_count}"
        assert worker_count >= 2, f"Expected at least 2 worker processes, found {worker_count}"

        # Check orchestrator output contains expected log prefixes
        # Read some output from orchestrator
        output_lines = []
        for _ in range(50):  # Read up to 50 lines or timeout
            try:
                line = orchestrator_proc.stdout.readline()
                if line:
                    output_lines.append(line.strip())
                    if len(output_lines) >= 10:  # Got enough output
                        break
                else:
                    time.sleep(0.1)
            except:
                break

        output_text = '\n'.join(output_lines)

        # Should see log prefixes like "scheduler |" and "worker-1 |", "worker-2 |"
        assert "scheduler |" in output_text or "scheduler:" in output_text, \
            f"Expected scheduler log prefix in output: {output_text}"

        # Should see worker prefixes
        worker_prefixes_found = any(
            prefix in output_text
            for prefix in ["worker-1 |", "worker-2 |", "worker-1:", "worker-2:"]
        )
        assert worker_prefixes_found, \
            f"Expected worker log prefixes in output: {output_text}"

    def test_graceful_shutdown_kills_children(self):
        """Test that killing orchestrator gracefully shuts down all children."""
        # This test will fail until graceful shutdown is implemented

        env = os.environ.copy()
        env['PORT'] = str(self.test_port)

        # Use temp file to avoid pipe buffer deadlock
        log_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.log')
        self.temp_files.append(log_file.name)

        orchestrator_proc = subprocess.Popen(
            [self.orchestrator_binary, "-workers", "2", "-scheduler-port", str(self.test_port)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent
        )
        self.processes.append(orchestrator_proc)

        # Wait for system to start
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, "Scheduler did not start within timeout"

        time.sleep(2)  # Let workers start

        # Count processes before shutdown
        initial_scheduler_count = self._count_processes_by_name("agentmaestro-scheduler")
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

        # Wait a moment for children cleanup
        time.sleep(2)

        # Verify no orphaned processes remain
        final_scheduler_count = self._count_processes_by_name("agentmaestro-scheduler")
        final_worker_count = self._count_processes_by_name("agentmaestro-worker")

        assert final_scheduler_count == 0, \
            f"Found {final_scheduler_count} orphaned scheduler processes after shutdown"
        assert final_worker_count == 0, \
            f"Found {final_worker_count} orphaned worker processes after shutdown"

    def test_child_crash_triggers_restart(self):
        """Test that killing a child process triggers orchestrator to restart it."""
        # This test will fail until child monitoring and restart is implemented

        env = os.environ.copy()
        env['PORT'] = str(self.test_port)

        orchestrator_proc = subprocess.Popen(
            [self.orchestrator_binary, "-workers", "2", "-scheduler-port", str(self.test_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent
        )
        self.processes.append(orchestrator_proc)

        # Wait for system to start
        scheduler_ready = self._wait_for_port(self.test_port, timeout=10)
        assert scheduler_ready, "Scheduler did not start within timeout"

        time.sleep(2)  # Let workers start

        # Find and kill one worker process
        worker_pid = None
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if "agentmaestro-worker" in cmdline:
                    worker_pid = proc.info['pid']
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        assert worker_pid is not None, "Could not find a worker process to kill"

        # Kill the worker
        os.kill(worker_pid, signal.SIGKILL)

        # Wait for orchestrator to detect and restart
        time.sleep(3)

        # Verify worker was restarted - should still have 2+ workers
        worker_count_after_restart = self._count_processes_by_name("agentmaestro-worker")
        assert worker_count_after_restart >= 2, \
            f"Expected at least 2 workers after restart, found {worker_count_after_restart}"

        # Verify scheduler still responding (wasn't affected by worker crash)
        response = requests.get(f"http://localhost:{self.test_port}/api/health", timeout=5)
        assert response.status_code == 200

    def test_log_aggregation_with_prefixes(self):
        """Test that logs from children are aggregated with colored prefixes."""
        # This test will fail until log aggregation is implemented

        env = os.environ.copy()
        env['PORT'] = str(self.test_port)

        orchestrator_proc = subprocess.Popen(
            [self.orchestrator_binary, "-workers", "1", "-scheduler-port", str(self.test_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent
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
            except:
                break

        output_text = '\n'.join(output_lines)

        # Should see docker-compose style prefixes
        expected_prefixes = ["scheduler |", "worker-1 |"]

        for prefix in expected_prefixes:
            assert prefix in output_text or prefix.replace(" |", ":") in output_text, \
                f"Expected prefix '{prefix}' in output: {output_text}"

        # Should not see raw unpredfixed logs from children
        # (This is harder to test definitively, but we can check structure)
        prefixed_lines = [line for line in output_lines
                         if any(p.split()[0] in line for p in expected_prefixes)]

        # Should have some prefixed lines
        assert len(prefixed_lines) > 0, \
            f"Expected some prefixed log lines, but found none in: {output_text}"

    def test_orchestrator_readiness_signal(self):
        """Test that orchestrator signals readiness once all children are started."""
        # This test will fail until readiness signaling is implemented

        env = os.environ.copy()
        env['PORT'] = str(self.test_port)

        # Use temp file to avoid pipe buffer deadlock
        log_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.log')
        self.temp_files.append(log_file.name)

        orchestrator_proc = subprocess.Popen(
            [self.orchestrator_binary, "-workers", "2", "-scheduler-port", str(self.test_port)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=Path(__file__).parent.parent.parent
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
                current_lines = current_content.strip().split('\n') if current_content.strip() else []

                for line in current_lines:
                    if line and line not in output_lines:
                        output_lines.append(line)
                        # Look for readiness signals
                        if any(phrase in line.lower() for phrase in ["ready", "started", "listening"]):
                            ready_signal_found = True
                            break

                if ready_signal_found:
                    break

                time.sleep(0.2)
            except Exception:
                break

        log_file.close()
        output_text = '\n'.join(output_lines)
        assert ready_signal_found, \
            f"Expected readiness signal in orchestrator output: {output_text}"

        # Verify system is actually ready by checking health endpoint
        response = requests.get(f"http://localhost:{self.test_port}/api/health", timeout=5)
        assert response.status_code == 200
