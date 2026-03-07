"""Tests validating Tier 1 build prerequisites.

Run with: uv run pytest tests/integration/test_build_prerequisites.py -v
"""

import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
SCHEDULER_BIN = BASE_DIR / "bin" / "agentbeacon"
WORKER_BIN = BASE_DIR / "bin" / "agentbeacon-worker"
CARGO_LOCK = BASE_DIR / "Cargo.lock"


def test_scheduler_version_flag():
    """agentbeacon --version reports a valid semver version."""
    result = subprocess.run(
        [str(SCHEDULER_BIN), "--version"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert re.search(r"agentbeacon \d+\.\d+\.\d+", result.stdout), (
        f"unexpected version output: {result.stdout!r}"
    )


def test_worker_version_flag():
    """agentbeacon-worker --version reports a valid semver version."""
    result = subprocess.run(
        [str(WORKER_BIN), "--version"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert re.search(r"agentbeacon-worker \d+\.\d+\.\d+", result.stdout), (
        f"unexpected version output: {result.stdout!r}"
    )


def test_version_flags_match():
    """Both binaries report the same version."""
    sched = subprocess.run(
        [str(SCHEDULER_BIN), "--version"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    worker = subprocess.run(
        [str(WORKER_BIN), "--version"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert sched.returncode == 0, f"scheduler --version failed: {sched.stderr}"
    assert worker.returncode == 0, f"worker --version failed: {worker.stderr}"
    sched_ver = sched.stdout.strip().split()[-1]
    worker_ver = worker.stdout.strip().split()[-1]
    assert sched_ver == worker_ver, (
        f"version mismatch: scheduler={sched_ver}, worker={worker_ver}"
    )


def test_no_openssl_in_dependency_tree():
    """Cargo.lock must not contain native-tls or openssl after rustls migration."""
    content = CARGO_LOCK.read_text()
    for banned in [
        "native-tls",
        "openssl",
        "openssl-sys",
        "openssl-macros",
        "openssl-probe",
        "tokio-native-tls",
    ]:
        assert f'name = "{banned}"' not in content, (
            f"Cargo.lock still contains {banned} — rustls migration incomplete"
        )


def test_mock_agent_script_resolves():
    """uv run mock-agent --help works (pyproject.toml scripts still functional)."""
    result = subprocess.run(
        ["uv", "run", "mock-agent", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(BASE_DIR),
    )
    assert result.returncode == 0, f"mock-agent --help failed: {result.stderr}"
