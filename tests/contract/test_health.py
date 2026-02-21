"""
T006: Contract test for Scheduler GET /api/health endpoint.

This test verifies that the scheduler binary serves the health endpoint correctly:
- Start scheduler binary directly
- Assert 200 status code
- Assert response body contains {"status": "healthy"}

Run with: uv run pytest -k test_health
"""

import time

import pytest
import requests

from tests.testhelpers import scheduler_context


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_health_endpoint_returns_200_with_status_ok(test_database):
    """Test that /api/health returns 200 with {"status": "healthy"}."""
    with scheduler_context(db_url=test_database) as ctx:
        response = requests.get(f"{ctx['url']}/api/health", timeout=5)

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}. Response: {response.text}"
        )
        assert response.json()["status"] == "healthy"
        assert response.headers.get("Content-Type") == "application/json", (
            f"Expected Content-Type application/json, got {response.headers.get('Content-Type')}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_health_endpoint_multiple_requests(test_database):
    """Test that health endpoint consistently returns correct response."""
    with scheduler_context(db_url=test_database) as ctx:
        for i in range(5):
            response = requests.get(f"{ctx['url']}/api/health", timeout=5)
            assert response.status_code == 200, (
                f"Request {i + 1}: Expected 200, got {response.status_code}"
            )
            assert response.json()["status"] == "healthy", (
                f"Request {i + 1}: Expected status 'healthy'"
            )
            time.sleep(0.1)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_health_endpoint_with_different_http_methods(test_database):
    """Test that health endpoint only accepts GET method."""
    with scheduler_context(db_url=test_database) as ctx:
        health_url = f"{ctx['url']}/api/health"

        get_response = requests.get(health_url, timeout=5)
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "healthy"

        post_response = requests.post(health_url, timeout=5)
        assert post_response.status_code == 405, (
            f"Expected 405 for POST, got {post_response.status_code}"
        )

        put_response = requests.put(health_url, timeout=5)
        assert put_response.status_code == 405, (
            f"Expected 405 for PUT, got {put_response.status_code}"
        )

        delete_response = requests.delete(health_url, timeout=5)
        assert delete_response.status_code == 405, (
            f"Expected 405 for DELETE, got {delete_response.status_code}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_health_endpoint_scheduler_startup_robustness(test_database):
    """Test that health endpoint is available immediately after scheduler starts."""
    with scheduler_context(db_url=test_database) as ctx:
        response = requests.get(f"{ctx['url']}/api/health", timeout=5)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        assert ctx["process"].poll() is None, (
            "Scheduler process should still be running"
        )
