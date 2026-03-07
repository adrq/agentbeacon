"""Verify musl-static binaries are truly static and correctly targeted.

These tests require musl binaries to be built first: make build-musl
Run with: uv run pytest -v -m musl
"""

import platform
import re
import subprocess
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent
BINARIES = ["agentbeacon", "agentbeacon-worker"]
MUSL_TARGETS = [
    ("x86_64-unknown-linux-musl", "x86-64"),
    ("aarch64-unknown-linux-musl", "ARM aarch64"),
]


def binary_path(target: str, name: str) -> Path:
    return BASE_DIR / "target" / target / "release" / name


@pytest.mark.musl
@pytest.mark.parametrize("binary_name", BINARIES)
@pytest.mark.parametrize("target,arch_label", MUSL_TARGETS)
def test_musl_binary_exists(target, arch_label, binary_name):
    """musl binary exists at expected path."""
    path = binary_path(target, binary_name)
    if not path.is_file():
        pytest.skip(f"Binary not built: {path}. Run 'make build-musl' first.")


@pytest.mark.musl
@pytest.mark.parametrize("binary_name", BINARIES)
@pytest.mark.parametrize("target,arch_label", MUSL_TARGETS)
def test_musl_binary_statically_linked(target, arch_label, binary_name):
    """musl binary has no dynamic dependencies (readelf -d shows no NEEDED entries)."""
    path = binary_path(target, binary_name)
    if not path.is_file():
        pytest.skip(f"Binary not built: {path}")
    result = subprocess.run(
        ["readelf", "-d", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, f"readelf failed: {result.stderr}"
    assert "NEEDED" not in result.stdout, (
        f"Binary has dynamic dependencies: {result.stdout}"
    )


@pytest.mark.musl
@pytest.mark.parametrize("binary_name", BINARIES)
@pytest.mark.parametrize("target,arch_label", MUSL_TARGETS)
def test_musl_binary_correct_architecture(target, arch_label, binary_name):
    """musl binary targets the correct CPU architecture."""
    path = binary_path(target, binary_name)
    if not path.is_file():
        pytest.skip(f"Binary not built: {path}")
    result = subprocess.run(
        ["file", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "ELF 64-bit" in result.stdout
    assert arch_label in result.stdout, f"Expected '{arch_label}' in: {result.stdout}"


@pytest.mark.musl
@pytest.mark.parametrize("binary_name", BINARIES)
def test_musl_x64_binary_runs(binary_name):
    """x86_64 musl binary can execute --version on this machine."""
    if platform.machine() not in ("x86_64", "AMD64"):
        pytest.skip(f"Cannot run x86_64 binary on {platform.machine()} host")
    path = binary_path("x86_64-unknown-linux-musl", binary_name)
    if not path.is_file():
        pytest.skip(f"Binary not built: {path}")
    result = subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert re.search(r"\d+\.\d+\.\d+", result.stdout), (
        f"No version in output: {result.stdout!r}"
    )


@pytest.mark.musl
def test_musl_x64_versions_match():
    """Both x86_64 musl binaries report the same version."""
    if platform.machine() not in ("x86_64", "AMD64"):
        pytest.skip(f"Cannot run x86_64 binary on {platform.machine()} host")
    versions = {}
    for name in BINARIES:
        path = binary_path("x86_64-unknown-linux-musl", name)
        if not path.is_file():
            pytest.skip(f"Binary not built: {path}")
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        versions[name] = result.stdout.strip().split()[-1]
    assert len(set(versions.values())) == 1, f"Version mismatch: {versions}"
