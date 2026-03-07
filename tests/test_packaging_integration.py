"""Integration tests for wheel generation and installation.

Requires musl binaries for the host architecture to be built.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import platform
import subprocess
import zipfile
from pathlib import Path

import pytest

# Import build_wheel.py from scripts/.
_spec = importlib.util.spec_from_file_location(
    "build_wheel",
    Path(__file__).resolve().parent.parent / "scripts" / "build_wheel.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_wheel = _mod.build_wheel
extract_version = _mod.extract_version
platform_tags = _mod.platform_tags
wheel_filename = _mod.wheel_filename
BINARIES = _mod.BINARIES
PACKAGE_NAME = _mod.PACKAGE_NAME

pytestmark = pytest.mark.packaging

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_PKG_DIR = REPO_ROOT / "python" / "agentbeacon"
CARGO_TOML = REPO_ROOT / "Cargo.toml"
VERSION = extract_version(CARGO_TOML)

# Map host architecture to Rust target triple.
_MACHINE_TO_TARGET = {
    "x86_64": "x86_64-unknown-linux-musl",
    "AMD64": "x86_64-unknown-linux-musl",
    "aarch64": "aarch64-unknown-linux-musl",
}

HOST_TARGET = _MACHINE_TO_TARGET.get(platform.machine())
BINARY_DIR = REPO_ROOT / "target" / HOST_TARGET / "release" if HOST_TARGET else None

HOST_MUSL_AVAILABLE = (
    HOST_TARGET is not None
    and BINARY_DIR is not None
    and all((BINARY_DIR / name).is_file() for name in BINARIES)
)

skip_no_host_musl = pytest.mark.skipif(
    not HOST_MUSL_AVAILABLE,
    reason=(
        f"musl binaries for host ({platform.machine()}) not built"
        if HOST_TARGET
        else f"Unsupported host architecture: {platform.machine()}"
    ),
)


@pytest.fixture
def built_wheel(tmp_path):
    """Build a real wheel from musl binaries matching the host architecture."""
    return build_wheel(
        target=HOST_TARGET,
        binary_dir=BINARY_DIR,
        python_pkg_dir=PYTHON_PKG_DIR,
        output_dir=tmp_path,
        cargo_toml=CARGO_TOML,
    )


@pytest.fixture
def installed_venv(tmp_path, built_wheel):
    """Create a temp venv with the wheel installed. Returns venv dir."""
    venv_dir = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            str(built_wheel),
            "--python",
            str(venv_dir / "bin" / "python"),
        ],
        check=True,
        capture_output=True,
    )
    return venv_dir


@skip_no_host_musl
def test_build_wheel_produces_valid_zip(built_wheel):
    assert built_wheel.is_file()
    assert zipfile.is_zipfile(built_wheel)


@skip_no_host_musl
def test_wheel_filename_correct(built_wheel):
    expected = wheel_filename(PACKAGE_NAME, VERSION, platform_tags(HOST_TARGET))
    assert built_wheel.name == expected


@skip_no_host_musl
def test_wheel_contains_expected_files(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())

    # Wrapper files
    assert "agentbeacon/__init__.py" in names
    assert "agentbeacon/__main__.py" in names
    assert "agentbeacon/_find_binary.py" in names

    # Binaries
    assert f"agentbeacon-{VERSION}.data/scripts/agentbeacon" in names
    assert f"agentbeacon-{VERSION}.data/scripts/agentbeacon-worker" in names

    # dist-info
    assert f"agentbeacon-{VERSION}.dist-info/METADATA" in names
    assert f"agentbeacon-{VERSION}.dist-info/WHEEL" in names
    assert f"agentbeacon-{VERSION}.dist-info/RECORD" in names
    assert f"agentbeacon-{VERSION}.dist-info/licenses/LICENSE" in names
    assert f"agentbeacon-{VERSION}.dist-info/licenses/NOTICE" in names


@skip_no_host_musl
def test_wheel_record_hashes_valid(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        record_data = zf.read(f"agentbeacon-{VERSION}.dist-info/RECORD").decode()
        for line in record_data.strip().split("\n"):
            parts = line.split(",")
            path = parts[0]
            if len(parts) < 3 or not parts[1]:
                # Self-entry (RECORD,,)
                assert path.endswith("RECORD")
                continue
            expected_hash = parts[1].split("=", 1)[1]
            expected_size = int(parts[2])
            actual_data = zf.read(path)
            actual_hash = (
                base64.urlsafe_b64encode(hashlib.sha256(actual_data).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            assert actual_hash == expected_hash, f"Hash mismatch for {path}"
            assert len(actual_data) == expected_size, f"Size mismatch for {path}"


@skip_no_host_musl
def test_wheel_binary_permissions(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        for name in BINARIES:
            arcname = f"agentbeacon-{VERSION}.data/scripts/{name}"
            info = zf.getinfo(arcname)
            unix_mode = (info.external_attr >> 16) & 0o777
            assert unix_mode == 0o755, (
                f"{arcname}: expected 0o755, got {oct(unix_mode)}"
            )


@skip_no_host_musl
def test_wheel_metadata_version_matches_cargo(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        metadata = zf.read(f"agentbeacon-{VERSION}.dist-info/METADATA").decode()
    for line in metadata.split("\n"):
        if line.startswith("Version: "):
            assert line == f"Version: {VERSION}"
            return
    pytest.fail("No Version: line in METADATA")


@skip_no_host_musl
def test_wheel_installs_in_venv(installed_venv):
    """Verify binaries and wrapper are installed."""
    assert (installed_venv / "bin" / "agentbeacon").is_file()
    assert (installed_venv / "bin" / "agentbeacon-worker").is_file()
    matches = list(
        (installed_venv / "lib").glob("python*/site-packages/agentbeacon/__main__.py")
    )
    assert len(matches) == 1


@skip_no_host_musl
def test_installed_agentbeacon_runs(installed_venv):
    """After install, agentbeacon --version outputs the version."""
    result = subprocess.run(
        [str(installed_venv / "bin" / "agentbeacon"), "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert f"agentbeacon {VERSION}" in result.stdout


@skip_no_host_musl
def test_installed_python_m_agentbeacon(tmp_path, installed_venv):
    """After install, python -m agentbeacon --version works."""
    # Run from tmp_path to avoid CWD's agentbeacon/ (mock agent) shadowing
    # the installed package.
    result = subprocess.run(
        [str(installed_venv / "bin" / "python"), "-m", "agentbeacon", "--version"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert f"agentbeacon {VERSION}" in result.stdout
