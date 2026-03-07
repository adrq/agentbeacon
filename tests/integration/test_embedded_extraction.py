"""Integration tests for embedded executor extraction.

Verifies that the worker binary extracts embedded executor JS files
to the data directory when AGENTBEACON_EXECUTORS_DIR is not set,
and skips extraction when the override is present.

Run with: uv run pytest tests/integration/test_embedded_extraction.py -v
"""

import os
import subprocess
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
WORKER_BIN = BASE_DIR / "bin" / "agentbeacon-worker"


def test_worker_extracts_embedded_executors():
    """Worker extracts embedded executors to data dir when no override set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = os.environ.copy()
        env["AGENTBEACON_DATA_DIR"] = tmpdir
        env.pop("AGENTBEACON_EXECUTORS_DIR", None)

        # Worker will fail to connect to scheduler (unreachable URL)
        # but extraction happens before connection attempt
        proc = subprocess.run(
            [
                str(WORKER_BIN),
                "--scheduler-url",
                "http://127.0.0.1:1",
                "--startup-max-attempts",
                "1",
                "--retry-delay",
                "100ms",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Worker should fail to connect to unreachable scheduler
        assert proc.returncode != 0, "worker should fail to connect"

        data_dir = Path(tmpdir)
        executors_dir = data_dir / "executors"

        assert executors_dir.exists(), (
            f"executors dir not created. stderr: {proc.stderr}"
        )
        assert (executors_dir / "claude-executor.js").exists()
        assert (executors_dir / "copilot-executor.js").exists()
        assert (executors_dir / "common" / "protocol.js").exists()
        assert (executors_dir / "common" / "stdio-bridge.js").exists()
        assert (data_dir / "package.json").exists()
        assert (data_dir / "package-lock.json").exists()
        assert (data_dir / ".version").exists()

        # Verify no mock files or source maps extracted
        for p in executors_dir.rglob("*"):
            assert not p.name.startswith("mock-"), f"mock file extracted: {p}"
            assert not p.name.endswith(".js.map"), f"source map extracted: {p}"
            assert not p.name.endswith(".d.ts"), f"type declaration extracted: {p}"


def test_override_skips_extraction():
    """When AGENTBEACON_EXECUTORS_DIR is set, embedded extraction is skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = os.environ.copy()
        env["AGENTBEACON_DATA_DIR"] = tmpdir
        env["AGENTBEACON_EXECUTORS_DIR"] = str(BASE_DIR / "executors" / "dist")

        _ = subprocess.run(
            [
                str(WORKER_BIN),
                "--scheduler-url",
                "http://127.0.0.1:1",
                "--startup-max-attempts",
                "1",
                "--retry-delay",
                "100ms",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        data_dir = Path(tmpdir)
        assert not (data_dir / "executors").exists(), (
            "extraction should be skipped with override"
        )
        assert not (data_dir / ".version").exists(), (
            "version marker should not exist with override"
        )
