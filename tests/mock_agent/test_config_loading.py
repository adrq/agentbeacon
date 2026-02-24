"""Tests for config file loading functionality.

These tests validate the --config flag behavior:
- Custom responses override defaults
- Graceful handling of missing/invalid config files
"""

import json
import tempfile
import os
import subprocess


def test_config_file_loading():
    """Test that custom responses load from JSON config file."""
    # Create temporary config file
    custom_responses = {
        "test_prompt": "custom_response_from_config",
        "hello": "world_from_config",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(custom_responses, f)
        config_file = f.name

    try:
        # Start mock agent with config
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "agentbeacon.mock_agent",
                "--mode",
                "stdio",
                "--config",
                config_file,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Test custom response
        proc.stdin.write("test_prompt\n")
        proc.stdin.flush()

        output_line = proc.stdout.readline()
        response = json.loads(output_line)

        # Should contain custom response in artifact text
        assert response["taskStatus"]["state"] == "completed"
        assert len(response["artifacts"]) > 0
        artifact_text = response["artifacts"][0]["parts"][0]["text"]
        assert "custom_response_from_config" in artifact_text

        # Test second custom response
        proc.stdin.write("hello\n")
        proc.stdin.flush()

        output_line = proc.stdout.readline()
        response = json.loads(output_line)

        artifact_text = response["artifacts"][0]["parts"][0]["text"]
        assert "world_from_config" in artifact_text

        # Cleanup
        proc.terminate()
        proc.wait(timeout=5)

    finally:
        os.unlink(config_file)


def test_config_file_not_found():
    """Test graceful handling when config file doesn't exist."""
    # Start with non-existent config file
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "agentbeacon.mock_agent",
            "--mode",
            "stdio",
            "--config",
            "/nonexistent/path/config.json",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Should start normally and use default responses
        proc.stdin.write("test_prompt\n")
        proc.stdin.flush()

        output_line = proc.stdout.readline()
        response = json.loads(output_line)

        # Should get default mock response
        assert response["taskStatus"]["state"] == "completed"
        assert len(response["artifacts"]) > 0
        artifact_text = response["artifacts"][0]["parts"][0]["text"]
        assert "Mock response: test_prompt" in artifact_text

    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_invalid_config_file():
    """Test handling of invalid JSON config file."""
    # Create invalid JSON file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ invalid json }")
        config_file = f.name

    try:
        # Should start normally despite invalid config
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "agentbeacon.mock_agent",
                "--mode",
                "stdio",
                "--config",
                config_file,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Should use default responses
        proc.stdin.write("test_prompt\n")
        proc.stdin.flush()

        output_line = proc.stdout.readline()
        response = json.loads(output_line)

        # Should get default mock response
        assert response["taskStatus"]["state"] == "completed"
        artifact_text = response["artifacts"][0]["parts"][0]["text"]
        assert "Mock response: test_prompt" in artifact_text

        proc.terminate()
        proc.wait(timeout=5)

    finally:
        os.unlink(config_file)
