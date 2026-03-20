"""Integration tests for the --setup flag.

Tests marked with @pytest.mark.npm require network access and npm on PATH.
Run all:  uv run pytest tests/integration/test_setup.py -v -m npm
Default:  uv run pytest tests/integration/test_setup.py -v  (skips npm tests)
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent.parent
WORKER_BIN = BASE_DIR / "bin" / "agentbeacon-worker"
SCHEDULER_BIN = BASE_DIR / "bin" / "agentbeacon"


def _run_setup(binary, *extra_args, data_dir=None):
    """Run a binary with --setup and optional extra args in a temp data dir."""
    env = os.environ.copy()
    if data_dir is not None:
        env["AGENTBEACON_DATA_DIR"] = str(data_dir)
    else:
        env.pop("AGENTBEACON_DATA_DIR", None)
    return subprocess.run(
        [str(binary), "--setup", *extra_args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_setup_status_shows_not_installed():
    """--setup --status shows 'not installed' for both drivers in a fresh dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _run_setup(WORKER_BIN, "--status", data_dir=tmpdir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "claude: not installed" in result.stdout
        assert "copilot: not installed" in result.stdout


def test_setup_extracts_before_status():
    """--setup --status extracts executor files before showing status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _run_setup(WORKER_BIN, "--status", data_dir=tmpdir)
        data_dir = Path(tmpdir)
        assert (data_dir / "package.json").exists()
        assert (data_dir / "package-lock.json").exists()
        assert (data_dir / "executors" / "claude-executor.js").exists()
        assert (data_dir / "executors" / "copilot-executor.js").exists()


def test_scheduler_setup_delegates_to_worker():
    """agentbeacon --setup --status delegates to worker and shows SDK status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _run_setup(SCHEDULER_BIN, "--status", data_dir=tmpdir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "claude:" in result.stdout
        assert "copilot:" in result.stdout


@pytest.mark.npm
def test_setup_installs_both_sdks():
    """--setup installs both SDKs via npm ci."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _run_setup(WORKER_BIN, data_dir=tmpdir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data_dir = Path(tmpdir)
        assert (
            data_dir
            / "node_modules"
            / "@anthropic-ai"
            / "claude-agent-sdk"
            / "package.json"
        ).exists()
        assert (
            data_dir / "node_modules" / "@github" / "copilot-sdk" / "package.json"
        ).exists()


@pytest.mark.npm
def test_setup_status_after_install():
    """After install, --status shows installed versions for both SDKs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _run_setup(WORKER_BIN, data_dir=tmpdir)
        result = _run_setup(WORKER_BIN, "--status", data_dir=tmpdir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "claude: installed (v" in result.stdout
        assert "copilot: installed (v" in result.stdout
