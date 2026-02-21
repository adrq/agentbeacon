"""
T007: Contract test for Scheduler GET /api/ready endpoint.

This test verifies that the scheduler binary serves the ready endpoint correctly:
- Start scheduler binary directly
- Assert 200 status code when ready
- Verify response format and method restrictions

Run with: uv run pytest -k test_ready
"""

import time

import pytest
import requests

from tests.testhelpers import scheduler_context


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ready_endpoint_eventually_returns_200(test_database):
    """Test that /api/ready eventually returns 200 when scheduler is ready."""
    with scheduler_context(db_url=test_database) as ctx:
        ready_response = None
        start_time = time.time()
        responses_seen = []

        while time.time() - start_time < 15:
            try:
                response = requests.get(f"{ctx['url']}/api/ready", timeout=5)
            except requests.RequestException:
                time.sleep(0.2)
                continue

            responses_seen.append((time.time() - start_time, response.status_code))

            if response.status_code == 200:
                ready_response = response
                break
            elif response.status_code == 503:
                pass
            else:
                pytest.fail(
                    f"Unexpected status code {response.status_code}, expected 200 or 503"
                )

            time.sleep(0.2)

        assert ready_response is not None, (
            f"Ready endpoint never returned 200. Responses seen: {responses_seen}"
        )
        assert ready_response.json()["status"] == "ready"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ready_endpoint_accepts_only_get_method(test_database):
    """Test that ready endpoint only accepts GET method."""
    with scheduler_context(db_url=test_database) as ctx:
        ready_url = f"{ctx['url']}/api/ready"

        get_response = requests.get(ready_url, timeout=5)
        assert get_response.status_code in [200, 503], (
            f"Expected 200 or 503 for GET, got {get_response.status_code}"
        )

        post_response = requests.post(ready_url, timeout=5)
        assert post_response.status_code == 405, (
            f"Expected 405 for POST, got {post_response.status_code}"
        )

        put_response = requests.put(ready_url, timeout=5)
        assert put_response.status_code == 405, (
            f"Expected 405 for PUT, got {put_response.status_code}"
        )

        delete_response = requests.delete(ready_url, timeout=5)
        assert delete_response.status_code == 405, (
            f"Expected 405 for DELETE, got {delete_response.status_code}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ready_endpoint_consistency_after_ready(test_database):
    """Test that ready endpoint consistently returns 200 once scheduler is ready."""
    with scheduler_context(db_url=test_database) as ctx:
        # scheduler_context already waited for health, ready should be immediate
        for i in range(5):
            response = requests.get(f"{ctx['url']}/api/ready", timeout=5)
            assert response.status_code == 200, (
                f"Request {i + 1}: Expected 200 after ready, got {response.status_code}"
            )
            assert response.json()["status"] == "ready", (
                f"Request {i + 1}: Expected status 'ready', got '{response.json().get('status')}'"
            )
            time.sleep(0.1)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ready_endpoint_response_format(test_database):
    """Test that ready endpoint response follows expected JSON format."""
    with scheduler_context(db_url=test_database) as ctx:
        response = requests.get(f"{ctx['url']}/api/ready", timeout=5)

        assert response.status_code in [200, 503], (
            f"Expected 200 or 503, got {response.status_code}"
        )
        assert response.headers.get("Content-Type") == "application/json", (
            f"Expected Content-Type application/json, got {response.headers.get('Content-Type')}"
        )

        response_json = response.json()
        assert isinstance(response_json, dict), (
            f"Expected JSON object, got {type(response_json)}"
        )
        assert isinstance(response_json.get("status"), str), (
            f"Expected status to be string, got {type(response_json.get('status'))}"
        )
