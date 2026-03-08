"""Integration tests for npm package generation and installation.

Requires musl binaries for the host architecture to be built.
Tests that install via npm also require node and npm on PATH.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

# Import build_npm.py from scripts/.
_spec = importlib.util.spec_from_file_location(
    "build_npm",
    Path(__file__).resolve().parent.parent / "scripts" / "build_npm.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_platform_package = _mod.build_platform_package
build_wrapper_package = _mod.build_wrapper_package
extract_version = _mod.extract_version
BINARIES = _mod.BINARIES
LICENSE_FILES = _mod.LICENSE_FILES

pytestmark = pytest.mark.npm

REPO_ROOT = Path(__file__).resolve().parent.parent
NPM_SOURCE_DIR = REPO_ROOT / "npm"
CARGO_TOML = REPO_ROOT / "Cargo.toml"
LICENSE_DIR = REPO_ROOT
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

NPM_AVAILABLE = shutil.which("node") is not None and shutil.which("npm") is not None

skip_no_host_musl = pytest.mark.skipif(
    not HOST_MUSL_AVAILABLE,
    reason=(
        f"musl binaries for host ({platform.machine()}) not built"
        if HOST_TARGET
        else f"Unsupported host architecture: {platform.machine()}"
    ),
)

skip_no_npm = pytest.mark.skipif(
    not NPM_AVAILABLE,
    reason="node and/or npm not available on PATH",
)


@pytest.fixture
def built_platform_package(tmp_path):
    """Build a real platform package from musl binaries matching the host architecture."""
    return build_platform_package(
        target=HOST_TARGET,
        binary_dir=BINARY_DIR,
        output_dir=tmp_path / "pkg",
        cargo_toml=CARGO_TOML,
        license_dir=LICENSE_DIR,
    )


@pytest.fixture
def built_wrapper_package(tmp_path):
    """Build a real wrapper package from the npm/ source directory."""
    return build_wrapper_package(
        npm_source_dir=NPM_SOURCE_DIR,
        output_dir=tmp_path / "pkg",
        cargo_toml=CARGO_TOML,
        license_dir=LICENSE_DIR,
    )


@pytest.fixture
def npm_installed(tmp_path, built_platform_package, built_wrapper_package):
    """Install both packages via npm from tarballs. Returns the install directory.

    Uses ``npm pack`` to create tarballs first — ``file:`` protocol creates
    symlinks, and ``require.resolve()`` follows the symlink back to the source
    location where sibling packages aren't resolvable. Tarballs are extracted
    as real copies, matching registry install behavior.
    """
    install_dir = tmp_path / "npm_test"
    install_dir.mkdir()

    # Pack both packages into tarballs.
    platform_result = subprocess.run(
        ["npm", "pack", str(built_platform_package)],
        cwd=install_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    platform_tarball = platform_result.stdout.strip().splitlines()[-1]

    wrapper_result = subprocess.run(
        ["npm", "pack", str(built_wrapper_package)],
        cwd=install_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    wrapper_tarball = wrapper_result.stdout.strip().splitlines()[-1]

    # Install from tarballs.
    (install_dir / "package.json").write_text('{"private": true}')
    subprocess.run(
        ["npm", "install", platform_tarball, wrapper_tarball],
        cwd=install_dir,
        check=True,
        capture_output=True,
    )
    return install_dir


# ---------------------------------------------------------------------------
# Package structure tests (require musl binaries only)
# ---------------------------------------------------------------------------


@skip_no_host_musl
def test_platform_package_has_correct_structure(built_platform_package):
    pkg_dir = built_platform_package
    assert (pkg_dir / "package.json").is_file()
    for name in BINARIES:
        assert (pkg_dir / "bin" / name).is_file()
    for fname in LICENSE_FILES:
        assert (pkg_dir / fname).is_file()


@skip_no_host_musl
def test_platform_package_json_valid(built_platform_package):
    data = json.loads((built_platform_package / "package.json").read_text())
    assert "name" in data
    assert "version" in data
    assert isinstance(data["os"], list)
    assert isinstance(data["cpu"], list)
    assert data["preferUnplugged"] is True


@skip_no_host_musl
def test_platform_package_version_matches_cargo(built_platform_package):
    data = json.loads((built_platform_package / "package.json").read_text())
    assert data["version"] == VERSION


@skip_no_host_musl
def test_wrapper_package_has_correct_structure(built_wrapper_package):
    pkg_dir = built_wrapper_package
    assert (pkg_dir / "package.json").is_file()
    assert (pkg_dir / "bin" / "agentbeacon.js").is_file()
    assert (pkg_dir / "bin" / "worker.js").is_file()
    assert (pkg_dir / "lib" / "resolve.js").is_file()
    for fname in LICENSE_FILES:
        assert (pkg_dir / fname).is_file()


@skip_no_host_musl
def test_wrapper_package_json_valid(built_wrapper_package):
    data = json.loads((built_wrapper_package / "package.json").read_text())
    assert data["name"] == "agentbeacon"
    assert "bin" in data
    assert "optionalDependencies" in data


@skip_no_host_musl
def test_wrapper_bin_scripts_have_shebang(built_wrapper_package):
    for script in ["bin/agentbeacon.js", "bin/worker.js"]:
        content = (built_wrapper_package / script).read_text()
        assert content.startswith("#!/usr/bin/env node"), f"{script} missing shebang"


# ---------------------------------------------------------------------------
# npm install + run tests (require musl binaries AND node/npm)
# ---------------------------------------------------------------------------


@skip_no_host_musl
@skip_no_npm
def test_npm_install_succeeds(npm_installed):
    assert (npm_installed / "node_modules").is_dir()


@skip_no_host_musl
@skip_no_npm
def test_npm_bin_links_created(npm_installed):
    bin_dir = npm_installed / "node_modules" / ".bin"
    assert (bin_dir / "agentbeacon").exists()
    assert (bin_dir / "agentbeacon-worker").exists()


@skip_no_host_musl
@skip_no_npm
def test_installed_agentbeacon_runs(npm_installed):
    result = subprocess.run(
        [str(npm_installed / "node_modules" / ".bin" / "agentbeacon"), "--version"],
        cwd=npm_installed,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert f"agentbeacon {VERSION}" in result.stdout


@skip_no_host_musl
@skip_no_npm
def test_installed_worker_runs(npm_installed):
    result = subprocess.run(
        [
            str(npm_installed / "node_modules" / ".bin" / "agentbeacon-worker"),
            "--version",
        ],
        cwd=npm_installed,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert f"agentbeacon-worker {VERSION}" in result.stdout
