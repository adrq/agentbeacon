"""
T006: Contract test for Scheduler GET /api/health endpoint.

This test verifies that the scheduler binary serves the health endpoint correctly:
- Start scheduler binary directly
- Assert 200 status code
- Assert response body contains {"status": "healthy"}

Run with: uv run pytest -k test_health
"""

import subprocess
import time
import tempfile
from tests.testhelpers import cleanup_processes
import pytest
import requests
from pathlib import Path


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
class TestSchedulerHealthEndpoint:
    """Contract tests for scheduler health endpoint."""

    def setup_method(self):
        """Set up test environment."""
        # Use a unique port for each test to avoid conflicts
        self.test_port = 19457  # Different from other tests
        self.scheduler_binary = "./bin/agentmaestro-scheduler"
        self.temp_dir = tempfile.mkdtemp()
        self.processes = []

    def teardown_method(self):
        """Clean up all processes and temporary files."""
        cleanup_processes(self.processes)

    def _wait_for_port(self, port, timeout=10, path="/api/health"):
        """Wait for a port to become available and return first response."""
        start_time = time.time()
        last_exception = None

        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"http://localhost:{port}{path}", timeout=1)
                return response
            except requests.RequestException as e:
                last_exception = e
                time.sleep(0.1)

        raise TimeoutError(
            f"Port {port} not available after {timeout}s. Last error: {last_exception}"
        )

    def test_health_endpoint_returns_200_with_status_ok(self, test_database):
        """Test that /api/health returns 200 with {"status": "healthy"}."""
        # Start the scheduler binary directly
        scheduler_proc = subprocess.Popen(
            [
                self.scheduler_binary,
                "--port",
                str(self.test_port),
                "--db-url",
                test_database,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(scheduler_proc)

        # Wait for scheduler to start and make first request
        response = self._wait_for_port(self.test_port, timeout=10)

        # Verify response
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}. Response: {response.text}"
        )

        # Verify response body
        response_json = response.json()
        assert "status" in response_json, (
            f"Expected 'status' field in response: {response_json}"
        )
        assert response_json["status"] == "healthy", (
            f"Expected status 'healthy', got '{response_json['status']}'"
        )

        # Verify Content-Type header
        assert response.headers.get("Content-Type") == "application/json", (
            f"Expected Content-Type application/json, got {response.headers.get('Content-Type')}"
        )

    def test_health_endpoint_multiple_requests(self, test_database):
        """Test that health endpoint consistently returns correct response."""
        # Start the scheduler binary directly
        scheduler_proc = subprocess.Popen(
            [
                self.scheduler_binary,
                "--port",
                str(self.test_port),
                "--db-url",
                test_database,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(scheduler_proc)

        # Wait for scheduler to start
        first_response = self._wait_for_port(self.test_port, timeout=10)
        assert first_response.status_code == 200

        # Make multiple requests to ensure consistency
        for i in range(5):
            response = requests.get(
                f"http://localhost:{self.test_port}/api/health", timeout=5
            )

            assert response.status_code == 200, (
                f"Request {i + 1}: Expected 200, got {response.status_code}"
            )

            response_json = response.json()
            assert response_json["status"] == "healthy", (
                f"Request {i + 1}: Expected status 'healthy', got '{response_json['status']}'"
            )

            # Small delay between requests
            time.sleep(0.1)

    def test_health_endpoint_with_different_http_methods(self, test_database):
        """Test that health endpoint only accepts GET method."""
        # Start the scheduler binary directly
        scheduler_proc = subprocess.Popen(
            [
                self.scheduler_binary,
                "--port",
                str(self.test_port),
                "--db-url",
                test_database,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(scheduler_proc)

        # Wait for scheduler to start
        first_response = self._wait_for_port(self.test_port, timeout=10)
        assert first_response.status_code == 200

        health_url = f"http://localhost:{self.test_port}/api/health"

        # Test GET (should work)
        get_response = requests.get(health_url, timeout=5)
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "healthy"

        # Test POST (should fail)
        post_response = requests.post(health_url, timeout=5)
        assert post_response.status_code == 405, (
            f"Expected 405 for POST, got {post_response.status_code}"
        )

        # Test PUT (should fail)
        put_response = requests.put(health_url, timeout=5)
        assert put_response.status_code == 405, (
            f"Expected 405 for PUT, got {put_response.status_code}"
        )

        # Test DELETE (should fail)
        delete_response = requests.delete(health_url, timeout=5)
        assert delete_response.status_code == 405, (
            f"Expected 405 for DELETE, got {delete_response.status_code}"
        )

    def test_health_endpoint_scheduler_startup_robustness(self, test_database):
        """Test that health endpoint is available immediately after scheduler starts."""
        # Start the scheduler binary directly
        scheduler_proc = subprocess.Popen(
            [
                self.scheduler_binary,
                "--port",
                str(self.test_port),
                "--db-url",
                test_database,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        self.processes.append(scheduler_proc)

        # The health endpoint should be available very quickly
        # This tests that health doesn't depend on expensive initialization
        response = self._wait_for_port(self.test_port, timeout=5)
        assert response.status_code == 200

        response_json = response.json()
        assert response_json["status"] == "healthy"

        # Verify scheduler is still running
        assert scheduler_proc.poll() is None, (
            "Scheduler process should still be running"
        )
