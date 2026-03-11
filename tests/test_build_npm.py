"""Unit tests for scripts/build_npm.py -- all pure functions."""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest

# Import build_npm.py from scripts/ (not a package).
_spec = importlib.util.spec_from_file_location(
    "build_npm",
    Path(__file__).resolve().parent.parent / "scripts" / "build_npm.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

extract_version = _mod.extract_version
target_to_platform = _mod.target_to_platform
platform_package_name = _mod.platform_package_name
generate_platform_package_json = _mod.generate_platform_package_json
generate_wrapper_package_json = _mod.generate_wrapper_package_json
build_platform_package = _mod.build_platform_package
build_wrapper_package = _mod.build_wrapper_package
TARGET_MAP = _mod.TARGET_MAP
BINARIES = _mod.BINARIES
WRAPPER_JS_FILES = _mod.WRAPPER_JS_FILES
LICENSE_FILES = _mod.LICENSE_FILES


# ---------------------------------------------------------------------------
# Version extraction
# ---------------------------------------------------------------------------


def test_extract_version(tmp_path):
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text('[workspace.package]\nversion = "3.2.1"\n')
    assert extract_version(cargo_toml) == "3.2.1"


def test_extract_version_missing_workspace_raises(tmp_path):
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[package]\nversion = "1.0.0"\n')
    with pytest.raises(ValueError, match="No \\[workspace\\.package\\]"):
        extract_version(cargo)


# ---------------------------------------------------------------------------
# Target helpers
# ---------------------------------------------------------------------------


def test_target_to_platform_x86_64():
    result = target_to_platform("x86_64-unknown-linux-musl")
    assert result == {"os": "linux", "cpu": "x64", "suffix": "linux-x64"}


def test_target_to_platform_aarch64():
    result = target_to_platform("aarch64-unknown-linux-musl")
    assert result == {"os": "linux", "cpu": "arm64", "suffix": "linux-arm64"}


def test_target_to_platform_unknown_raises():
    with pytest.raises(ValueError, match="Unknown target"):
        target_to_platform("powerpc-unknown-linux-musl")


def test_platform_package_name_x86_64():
    assert (
        platform_package_name("x86_64-unknown-linux-musl")
        == "@agentbeacon/cli-linux-x64"
    )


def test_platform_package_name_aarch64():
    assert (
        platform_package_name("aarch64-unknown-linux-musl")
        == "@agentbeacon/cli-linux-arm64"
    )


# ---------------------------------------------------------------------------
# Platform package.json
# ---------------------------------------------------------------------------


def test_platform_package_json_fields():
    pkg_json = json.loads(
        generate_platform_package_json("x86_64-unknown-linux-musl", "1.2.3")
    )
    assert pkg_json["name"] == "@agentbeacon/cli-linux-x64"
    assert pkg_json["version"] == "1.2.3"
    assert pkg_json["license"] == "Apache-2.0"
    assert pkg_json["preferUnplugged"] is True
    assert pkg_json["publishConfig"] == {"access": "public"}
    assert pkg_json["files"] == ["bin/", "LICENSE", "NOTICE"]


def test_platform_package_json_os_cpu_are_arrays():
    pkg_json = json.loads(
        generate_platform_package_json("x86_64-unknown-linux-musl", "0.1.0")
    )
    assert isinstance(pkg_json["os"], list)
    assert isinstance(pkg_json["cpu"], list)
    assert pkg_json["os"] == ["linux"]
    assert pkg_json["cpu"] == ["x64"]


# ---------------------------------------------------------------------------
# Wrapper package.json
# ---------------------------------------------------------------------------


def test_wrapper_package_json_fields():
    pkg_json = json.loads(generate_wrapper_package_json("0.1.0"))
    assert pkg_json["name"] == "agentbeacon"
    assert pkg_json["version"] == "0.1.0"
    assert pkg_json["license"] == "Apache-2.0"
    assert pkg_json["files"] == ["bin/", "lib/", "LICENSE", "NOTICE"]
    assert pkg_json["publishConfig"] == {"access": "public"}
    assert "optionalDependencies" in pkg_json


def test_wrapper_package_json_bin_entries():
    pkg_json = json.loads(generate_wrapper_package_json("0.1.0"))
    assert pkg_json["bin"] == {
        "agentbeacon": "bin/agentbeacon.js",
        "agentbeacon-worker": "bin/worker.js",
    }


def test_wrapper_optional_deps_match_platforms():
    pkg_json = json.loads(generate_wrapper_package_json("0.1.0"))
    opt_deps = pkg_json["optionalDependencies"]
    for target in TARGET_MAP:
        pkg_name = platform_package_name(target)
        assert pkg_name in opt_deps, f"{pkg_name} missing from optionalDependencies"


def test_wrapper_optional_deps_version_sync():
    version = "2.5.0"
    pkg_json = json.loads(generate_wrapper_package_json(version))
    for dep_version in pkg_json["optionalDependencies"].values():
        assert dep_version == version


# ---------------------------------------------------------------------------
# build_platform_package
# ---------------------------------------------------------------------------


def _make_stub_binaries(binary_dir: Path) -> None:
    """Create tiny placeholder binaries."""
    binary_dir.mkdir(parents=True, exist_ok=True)
    for name in BINARIES:
        p = binary_dir / name
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_stub_license(license_dir: Path) -> None:
    """Create stub LICENSE and NOTICE files."""
    for fname in LICENSE_FILES:
        (license_dir / fname).write_text(f"stub {fname}")


def _make_stub_cargo_toml(directory: Path) -> Path:
    cargo = directory / "Cargo.toml"
    cargo.write_text('[workspace.package]\nversion = "0.1.0"\n')
    return cargo


def test_build_platform_package_structure(tmp_path):
    binary_dir = tmp_path / "bin"
    _make_stub_binaries(binary_dir)
    _make_stub_license(tmp_path)
    cargo = _make_stub_cargo_toml(tmp_path)

    pkg_dir = build_platform_package(
        target="x86_64-unknown-linux-musl",
        binary_dir=binary_dir,
        output_dir=tmp_path / "out",
        cargo_toml=cargo,
        license_dir=tmp_path,
    )
    assert (pkg_dir / "package.json").is_file()
    assert (pkg_dir / "bin" / "agentbeacon").is_file()
    assert (pkg_dir / "bin" / "agentbeacon-worker").is_file()
    assert (pkg_dir / "LICENSE").is_file()
    assert (pkg_dir / "NOTICE").is_file()


def test_build_platform_package_binary_executable(tmp_path):
    binary_dir = tmp_path / "bin"
    _make_stub_binaries(binary_dir)
    _make_stub_license(tmp_path)
    cargo = _make_stub_cargo_toml(tmp_path)

    pkg_dir = build_platform_package(
        target="x86_64-unknown-linux-musl",
        binary_dir=binary_dir,
        output_dir=tmp_path / "out",
        cargo_toml=cargo,
        license_dir=tmp_path,
    )
    for name in BINARIES:
        mode = (pkg_dir / "bin" / name).stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} is not executable"


def test_build_platform_package_missing_binary_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    _make_stub_license(tmp_path)
    cargo = _make_stub_cargo_toml(tmp_path)

    with pytest.raises(FileNotFoundError, match="Binary not found"):
        build_platform_package(
            target="x86_64-unknown-linux-musl",
            binary_dir=empty_dir,
            output_dir=tmp_path / "out",
            cargo_toml=cargo,
            license_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# build_wrapper_package
# ---------------------------------------------------------------------------


def _make_stub_npm_source(npm_dir: Path) -> None:
    """Create stub JS source files matching WRAPPER_JS_FILES."""
    for rel_path in WRAPPER_JS_FILES:
        p = npm_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"// stub {rel_path}")


def test_build_wrapper_package_structure(tmp_path):
    npm_dir = tmp_path / "npm"
    _make_stub_npm_source(npm_dir)
    _make_stub_license(tmp_path)
    cargo = _make_stub_cargo_toml(tmp_path)

    pkg_dir = build_wrapper_package(
        npm_source_dir=npm_dir,
        output_dir=tmp_path / "out",
        cargo_toml=cargo,
        license_dir=tmp_path,
    )
    assert (pkg_dir / "package.json").is_file()
    assert (pkg_dir / "bin" / "agentbeacon.js").is_file()
    assert (pkg_dir / "bin" / "worker.js").is_file()
    assert (pkg_dir / "lib" / "resolve.js").is_file()
    assert (pkg_dir / "LICENSE").is_file()
    assert (pkg_dir / "NOTICE").is_file()


def test_build_wrapper_package_missing_source_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    _make_stub_license(tmp_path)
    cargo = _make_stub_cargo_toml(tmp_path)

    with pytest.raises(FileNotFoundError, match="JS source file not found"):
        build_wrapper_package(
            npm_source_dir=empty_dir,
            output_dir=tmp_path / "out",
            cargo_toml=cargo,
            license_dir=tmp_path,
        )
