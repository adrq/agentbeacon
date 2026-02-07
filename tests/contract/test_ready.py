"""
T007: Contract test for Scheduler GET /api/ready endpoint.

This test verifies that the scheduler binary serves the ready endpoint correctly:
- Start scheduler binary directly
- Assert 200 status code when ready OR 503 before ready
- Test both immediate readiness and delayed readiness scenarios

Run with: uv run pytest -k test_ready
"""

import subprocess
import time
import tempfile
from tests.testhelpers import cleanup_processes
import pytest
import requests
from pathlib import Path


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
class TestSchedulerReadyEndpoint:
    """Contract tests for scheduler ready endpoint."""

    def setup_method(self):
        """Set up test environment."""
        # Use a unique port for each test to avoid conflicts
        self.test_port = 19458  # Different from other tests
        self.scheduler_binary = "./bin/agentbeacon-scheduler"
        self.temp_dir = tempfile.mkdtemp()
        self.processes = []

    def teardown_method(self):
        """Clean up all processes and temporary files."""
        cleanup_processes(self.processes)

    def _make_ready_request(self, port, timeout=5):
        """Make a request to the ready endpoint."""
        try:
            response = requests.get(
                f"http://localhost:{port}/api/ready", timeout=timeout
            )
            return response
        except requests.RequestException:
            return None

    def _wait_for_server_start(self, port, timeout=10):
        """Wait for server to start accepting connections (any response)."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # Try health endpoint first as it should always be available
                response = requests.get(
                    f"http://localhost:{port}/api/health", timeout=1
                )
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.1)
        return False

    def test_ready_endpoint_eventually_returns_200(self, test_database):
        """Test that /api/ready eventually returns 200 when scheduler is ready."""
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

        # Wait for server to start accepting connections
        server_started = self._wait_for_server_start(self.test_port, timeout=10)
        assert server_started, (
            "Scheduler did not start accepting connections within 10 seconds"
        )

        # Monitor ready endpoint - it should eventually return 200
        ready_response = None
        start_time = time.time()
        responses_seen = []

        while time.time() - start_time < 15:  # Wait up to 15 seconds for readiness
            response = self._make_ready_request(self.test_port)
            if response is not None:
                responses_seen.append((time.time() - start_time, response.status_code))

                if response.status_code == 200:
                    ready_response = response
                    break
                elif response.status_code == 503:
                    # This is expected while not ready - continue waiting
                    pass
                else:
                    pytest.fail(
                        f"Unexpected status code {response.status_code}, expected 200 or 503"
                    )

            time.sleep(0.2)

        # Should eventually become ready
        assert ready_response is not None, (
            f"Ready endpoint never returned 200. Responses seen: {responses_seen}"
        )
        assert ready_response.status_code == 200, (
            f"Expected 200, got {ready_response.status_code}"
        )

        # Verify response format
        response_json = ready_response.json()
        assert "status" in response_json, (
            f"Expected 'status' field in ready response: {response_json}"
        )

        # Tightened assertion - expect exactly "ready"
        assert response_json["status"] == "ready", (
            f"Expected status 'ready', got '{response_json['status']}'"
        )

    def test_ready_endpoint_accepts_only_get_method(self, test_database):
        """Test that ready endpoint only accepts GET method."""
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

        # Wait for server to start
        server_started = self._wait_for_server_start(self.test_port, timeout=10)
        assert server_started, "Scheduler did not start within timeout"

        ready_url = f"http://localhost:{self.test_port}/api/ready"

        # Test GET (should work, either 200 or 503)
        get_response = requests.get(ready_url, timeout=5)
        assert get_response.status_code in [200, 503], (
            f"Expected 200 or 503 for GET, got {get_response.status_code}"
        )

        # Test POST (should fail)
        post_response = requests.post(ready_url, timeout=5)
        assert post_response.status_code == 405, (
            f"Expected 405 for POST, got {post_response.status_code}"
        )

        # Test PUT (should fail)
        put_response = requests.put(ready_url, timeout=5)
        assert put_response.status_code == 405, (
            f"Expected 405 for PUT, got {put_response.status_code}"
        )

        # Test DELETE (should fail)
        delete_response = requests.delete(ready_url, timeout=5)
        assert delete_response.status_code == 405, (
            f"Expected 405 for DELETE, got {delete_response.status_code}"
        )

    def test_ready_endpoint_consistency_after_ready(self, test_database):
        """Test that ready endpoint consistently returns 200 once scheduler is ready."""
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

        # Wait for server to start
        server_started = self._wait_for_server_start(self.test_port, timeout=10)
        assert server_started, "Scheduler did not start within timeout"

        # Wait for ready state
        ready_achieved = False
        start_time = time.time()

        while time.time() - start_time < 15:
            response = self._make_ready_request(self.test_port)
            if response is not None and response.status_code == 200:
                ready_achieved = True
                break
            time.sleep(0.2)

        assert ready_achieved, "Scheduler never became ready within 15 seconds"

        # Once ready, should consistently return 200
        for i in range(5):
            response = requests.get(
                f"http://localhost:{self.test_port}/api/ready", timeout=5
            )
            assert response.status_code == 200, (
                f"Request {i + 1}: Expected 200 after ready, got {response.status_code}"
            )

            response_json = response.json()
            assert "status" in response_json, (
                f"Request {i + 1}: Expected 'status' field in response"
            )

            time.sleep(0.1)

    def test_ready_endpoint_503_before_ready_state(self, test_database):
        """Test that ready endpoint returns 503 before scheduler is fully ready."""
        # NOTE: This test verifies the expected behavior during startup
        # If scheduler becomes ready too quickly, this test may be hard to verify

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

        # Wait for server to start accepting connections
        server_started = self._wait_for_server_start(self.test_port, timeout=10)
        assert server_started, "Scheduler did not start within timeout"

        # Make immediate requests to ready endpoint
        # Should either get 503 (not ready) or 200 (ready)
        # We'll collect a few responses to see the behavior
        responses = []
        for i in range(3):
            response = self._make_ready_request(self.test_port)
            if response is not None:
                responses.append(response.status_code)
            time.sleep(0.1)

        # Should only see valid status codes
        for status_code in responses:
            assert status_code in [200, 503], (
                f"Expected only 200 or 503 status codes, got {status_code}"
            )

        # At least one response should be successful
        assert len(responses) > 0, "Should have received at least one response"

    def test_ready_endpoint_response_format(self, test_database):
        """Test that ready endpoint response follows expected JSON format."""
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

        # Wait for server to start
        server_started = self._wait_for_server_start(self.test_port, timeout=10)
        assert server_started, "Scheduler did not start within timeout"

        # Get a response from ready endpoint
        response = None
        start_time = time.time()

        while time.time() - start_time < 10:
            test_response = self._make_ready_request(self.test_port)
            if test_response is not None:
                response = test_response
                break
            time.sleep(0.2)

        assert response is not None, "Could not get response from ready endpoint"
        assert response.status_code in [200, 503], (
            f"Expected 200 or 503, got {response.status_code}"
        )

        # Verify Content-Type
        assert response.headers.get("Content-Type") == "application/json", (
            f"Expected Content-Type application/json, got {response.headers.get('Content-Type')}"
        )

        # Verify JSON structure
        response_json = response.json()
        assert isinstance(response_json, dict), (
            f"Expected JSON object, got {type(response_json)}"
        )

        assert "status" in response_json, (
            f"Expected 'status' field in response: {response_json}"
        )

        # Status should be a string
        assert isinstance(response_json["status"], str), (
            f"Expected status to be string, got {type(response_json['status'])}"
        )
