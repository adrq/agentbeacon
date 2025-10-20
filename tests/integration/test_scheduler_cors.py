"""CORS security integration tests for scheduler.

Validates that the scheduler enforces restricted CORS policies to prevent
unauthorized cross-origin requests and credential theft.
"""

import os

import pytest
import requests

from tests.testhelpers import scheduler_context


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cors_blocks_unauthorized_origin(test_database):
    """Test that scheduler rejects requests from unauthorized origins."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        headers = {
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        }

        response = requests.options(
            f"{scheduler_url}/api/health",
            headers=headers,
            timeout=5,
        )

        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        assert allow_origin != "http://evil.example.com", (
            f"Scheduler should not allow evil.example.com origin, got: {allow_origin}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cors_allows_scheduler_own_origin(test_database):
    """Test that scheduler allows requests from its own origin."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        port = scheduler["port"]

        headers = {
            "Origin": f"http://localhost:{port}",
            "Access-Control-Request-Method": "POST",
        }

        response = requests.options(
            f"{scheduler_url}/api/health",
            headers=headers,
            timeout=5,
        )

        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        assert allow_origin == f"http://localhost:{port}", (
            f"Scheduler should allow its own origin, got: {allow_origin}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cors_allows_vite_dev_server_in_dev_mode(test_database):
    """Test that scheduler allows Vite dev server (port 5173) in DEV_MODE."""
    original_dev_mode = os.environ.get("DEV_MODE")
    try:
        os.environ["DEV_MODE"] = "1"

        with scheduler_context(db_url=test_database) as scheduler:
            scheduler_url = scheduler["url"]

            headers = {
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            }

            response = requests.options(
                f"{scheduler_url}/api/health",
                headers=headers,
                timeout=5,
            )

            allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
            assert allow_origin == "http://localhost:5173", (
                f"Scheduler in DEV_MODE should allow Vite origin, got: {allow_origin}"
            )
    finally:
        if original_dev_mode is None:
            os.environ.pop("DEV_MODE", None)
        else:
            os.environ["DEV_MODE"] = original_dev_mode


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cors_custom_origins_via_env_var(test_database):
    """Test that scheduler allows custom origins via CORS_ALLOWED_ORIGINS."""
    original_cors_origins = os.environ.get("CORS_ALLOWED_ORIGINS")
    try:
        os.environ["CORS_ALLOWED_ORIGINS"] = (
            "http://localhost:3000,http://localhost:8080"
        )

        with scheduler_context(db_url=test_database) as scheduler:
            scheduler_url = scheduler["url"]

            headers = {
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            }

            response = requests.options(
                f"{scheduler_url}/api/health",
                headers=headers,
                timeout=5,
            )

            allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
            assert allow_origin == "http://localhost:3000", (
                f"Should allow custom origin localhost:3000, got: {allow_origin}"
            )

            headers["Origin"] = "http://localhost:8080"
            response = requests.options(
                f"{scheduler_url}/api/health",
                headers=headers,
                timeout=5,
            )

            allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
            assert allow_origin == "http://localhost:8080", (
                f"Should allow custom origin localhost:8080, got: {allow_origin}"
            )

            headers["Origin"] = "http://localhost:9999"
            response = requests.options(
                f"{scheduler_url}/api/health",
                headers=headers,
                timeout=5,
            )

            allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
            assert allow_origin != "http://localhost:9999", (
                f"Should NOT allow unauthorized origin localhost:9999, got: {allow_origin}"
            )

    finally:
        if original_cors_origins is None:
            os.environ.pop("CORS_ALLOWED_ORIGINS", None)
        else:
            os.environ["CORS_ALLOWED_ORIGINS"] = original_cors_origins


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cors_blocks_wildcard_origin(test_database):
    """Test that scheduler does not use wildcard (*) CORS origin."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        test_origins = [
            "http://evil.example.com",
            "https://attacker.com",
            "http://localhost:6666",
        ]

        for origin in test_origins:
            headers = {
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            }

            response = requests.options(
                f"{scheduler_url}/api/health",
                headers=headers,
                timeout=5,
            )

            allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
            assert allow_origin != "*", "Scheduler must not use wildcard CORS origin"
            assert allow_origin != origin, (
                f"Scheduler should not allow unauthorized origin {origin}"
            )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_cors_actual_api_request_respects_origin(test_database):
    """Test that actual API requests (not just preflight) respect CORS."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        port = scheduler["port"]

        headers = {
            "Origin": f"http://localhost:{port}",
            "Content-Type": "application/json",
        }

        response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            headers=headers,
            timeout=5,
        )

        assert response.status_code == 200
        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        assert allow_origin == f"http://localhost:{port}", (
            f"Actual request should include CORS allow origin, got: {allow_origin}"
        )

        headers["Origin"] = "http://evil.example.com"
        response = requests.post(
            f"{scheduler_url}/api/worker/sync",
            json={"status": "idle"},
            headers=headers,
            timeout=5,
        )

        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        assert allow_origin != "http://evil.example.com", (
            f"Should not allow evil origin in response, got: {allow_origin}"
        )
