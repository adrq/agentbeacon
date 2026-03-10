"""Unit tests for scripts/publish.py -- all pure functions."""

from __future__ import annotations

import hashlib
import importlib.util
import tarfile
from pathlib import Path

import pytest

# Import publish.py from scripts/ (not a package).
_spec = importlib.util.spec_from_file_location(
    "publish",
    Path(__file__).resolve().parent.parent / "scripts" / "publish.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

semver_to_pep440 = _mod.semver_to_pep440
is_prerelease = _mod.is_prerelease
extract_version = _mod.extract_version
require_tool = _mod.require_tool
tarball_name = _mod.tarball_name
build_tarball = _mod.build_tarball
sha256_hex = _mod.sha256_hex
generate_checksums = _mod.generate_checksums
find_wheels = _mod.find_wheels
validate_wheel_set = _mod.validate_wheel_set
find_npm_packages = _mod.find_npm_packages
validate_npm_platform_set = _mod.validate_npm_platform_set
validate_npm_versions = _mod.validate_npm_versions
run_cmd = _mod.run_cmd
BINARIES = _mod.BINARIES
NPM_SCOPE = _mod.NPM_SCOPE
PACKAGE_NAME = _mod.PACKAGE_NAME
TARGETS = _mod.TARGETS
TARGET_TO_NPM_SUFFIX = _mod.TARGET_TO_NPM_SUFFIX
TARGET_TO_WHEEL_TAG = _mod.TARGET_TO_WHEEL_TAG


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


def _make_stub_binaries(binary_dir: Path) -> None:
    binary_dir.mkdir(parents=True, exist_ok=True)
    for name in BINARIES:
        p = binary_dir / name
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        p.chmod(0o755)


def _make_stub_wheels(dist_dir: Path) -> list[Path]:
    dist_dir.mkdir(parents=True, exist_ok=True)
    names = [
        "agentbeacon-0.1.0-py3-none-manylinux_2_17_x86_64.whl",
        "agentbeacon-0.1.0-py3-none-manylinux_2_17_aarch64.whl",
    ]
    paths = []
    for name in names:
        p = dist_dir / name
        p.write_bytes(b"PK\x03\x04")
        paths.append(p)
    return paths


def _make_stub_npm_packages(dist_npm_dir: Path, version: str = "0.1.0") -> None:
    for suffix in ["linux-x64", "linux-arm64"]:
        pkg_dir = dist_npm_dir / NPM_SCOPE / f"cli-{suffix}"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        name = f"{NPM_SCOPE}/cli-{suffix}"
        (pkg_dir / "package.json").write_text(
            f'{{"name": "{name}", "version": "{version}"}}'
        )
    wrapper_dir = dist_npm_dir / PACKAGE_NAME
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    (wrapper_dir / "package.json").write_text(
        f'{{"name": "agentbeacon", "version": "{version}"}}'
    )


# ---------------------------------------------------------------------------
# Version extraction
# ---------------------------------------------------------------------------


def test_extract_version(tmp_path):
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[workspace.package]\nversion = "3.2.1"\n')
    assert extract_version(cargo) == "3.2.1"


def test_extract_version_missing_workspace_raises(tmp_path):
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[package]\nversion = "1.0.0"\n')
    with pytest.raises(ValueError, match="No \\[workspace\\.package\\]"):
        extract_version(cargo)


# ---------------------------------------------------------------------------
# SemVer to PEP 440
# ---------------------------------------------------------------------------


def test_semver_to_pep440_stable():
    assert semver_to_pep440("1.0.0") == "1.0.0"


def test_semver_to_pep440_alpha():
    assert semver_to_pep440("1.0.0-alpha.1") == "1.0.0a1"


def test_semver_to_pep440_beta():
    assert semver_to_pep440("2.1.0-beta.3") == "2.1.0b3"


def test_semver_to_pep440_rc():
    assert semver_to_pep440("0.5.0-rc.1") == "0.5.0rc1"


# ---------------------------------------------------------------------------
# Pre-release detection
# ---------------------------------------------------------------------------


def test_is_prerelease_stable():
    assert is_prerelease("1.0.0") is False


def test_is_prerelease_alpha():
    assert is_prerelease("1.0.0-alpha.1") is True


def test_is_prerelease_beta():
    assert is_prerelease("2.1.0-beta.3") is True


def test_is_prerelease_rc():
    assert is_prerelease("0.5.0-rc.1") is True


# ---------------------------------------------------------------------------
# Wheel discovery with PEP 440 pre-release
# ---------------------------------------------------------------------------


def test_find_wheels_pep440_prerelease(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    # build_wheel.py produces PEP 440 names (alpha.1 -> a1).
    whl = tmp_path / "agentbeacon-1.0.0a1-py3-none-manylinux_2_17_x86_64.whl"
    whl.write_bytes(b"PK\x03\x04")
    # publish.py converts raw SemVer to PEP 440 before calling find_wheels.
    found = find_wheels(tmp_path, semver_to_pep440("1.0.0-alpha.1"))
    assert len(found) == 1
    assert found[0].name == whl.name


# ---------------------------------------------------------------------------
# Tarball names
# ---------------------------------------------------------------------------


def test_tarball_name_format():
    assert (
        tarball_name("0.1.0", "x86_64-unknown-linux-musl")
        == "agentbeacon-0.1.0-x86_64-unknown-linux-musl.tar.gz"
    )


def test_tarball_name_all_targets():
    names = [tarball_name("0.1.0", t) for t in TARGETS]
    assert len(names) == len(set(names)), "Tarball names must be distinct per target"


# ---------------------------------------------------------------------------
# Tarball building
# ---------------------------------------------------------------------------


def test_build_tarball_contents(tmp_path):
    binary_dir = tmp_path / "bin"
    _make_stub_binaries(binary_dir)
    tb = build_tarball(
        "0.1.0", "x86_64-unknown-linux-musl", binary_dir, tmp_path / "out"
    )
    with tarfile.open(tb, "r:gz") as tar:
        members = sorted(tar.getnames())
    assert members == sorted(BINARIES)


def test_build_tarball_permissions(tmp_path):
    binary_dir = tmp_path / "bin"
    _make_stub_binaries(binary_dir)
    tb = build_tarball(
        "0.1.0", "x86_64-unknown-linux-musl", binary_dir, tmp_path / "out"
    )
    with tarfile.open(tb, "r:gz") as tar:
        for member in tar.getmembers():
            assert member.mode == 0o755, f"{member.name} has mode {oct(member.mode)}"


def test_build_tarball_reproducible(tmp_path):
    binary_dir = tmp_path / "bin"
    _make_stub_binaries(binary_dir)
    tb1 = build_tarball(
        "0.1.0", "x86_64-unknown-linux-musl", binary_dir, tmp_path / "out1"
    )
    tb2 = build_tarball(
        "0.1.0", "x86_64-unknown-linux-musl", binary_dir, tmp_path / "out2"
    )
    assert tb1.read_bytes() == tb2.read_bytes()


def test_build_tarball_missing_binary_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="Binary not found"):
        build_tarball("0.1.0", "x86_64-unknown-linux-musl", empty_dir, tmp_path / "out")


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------


def test_sha256_hex_known_value(tmp_path):
    p = tmp_path / "test.bin"
    data = b"hello world"
    p.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert sha256_hex(p) == expected


def test_generate_checksums_format(tmp_path):
    p1 = tmp_path / "a.tar.gz"
    p2 = tmp_path / "b.tar.gz"
    p1.write_bytes(b"aaa")
    p2.write_bytes(b"bbb")
    cs = generate_checksums([p1, p2], tmp_path / "out")
    text = cs.read_text()
    lines = text.strip().split("\n")
    for line in lines:
        # Format: <64-char-hex>  <filename>
        parts = line.split("  ", 1)
        assert len(parts) == 2, f"Expected two-space separator in: {line}"
        assert len(parts[0]) == 64, f"Hash should be 64 hex chars: {parts[0]}"


def test_generate_checksums_includes_all_files(tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"file{i}.tar.gz"
        p.write_bytes(f"content{i}".encode())
        files.append(p)
    cs = generate_checksums(files, tmp_path / "out")
    lines = cs.read_text().strip().split("\n")
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# Wheel discovery
# ---------------------------------------------------------------------------


def test_find_wheels_finds_whl_files(tmp_path):
    wheels = _make_stub_wheels(tmp_path)
    found = find_wheels(tmp_path, "0.1.0")
    assert len(found) == 2
    assert found == sorted(wheels)


def test_find_wheels_empty_dir_raises(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(FileNotFoundError, match="No .whl files"):
        find_wheels(tmp_path, "0.1.0")


def test_find_wheels_ignores_other_versions(tmp_path):
    _make_stub_wheels(tmp_path)
    # Add a wheel for a different version.
    stale = tmp_path / "agentbeacon-0.0.9-py3-none-manylinux_2_17_x86_64.whl"
    stale.write_bytes(b"PK\x03\x04")
    found = find_wheels(tmp_path, "0.1.0")
    assert len(found) == 2
    assert all("0.1.0" in f.name for f in found)


# ---------------------------------------------------------------------------
# Wheel set validation
# ---------------------------------------------------------------------------


def test_validate_wheel_set_complete(tmp_path):
    wheels = _make_stub_wheels(tmp_path)
    validate_wheel_set(wheels)


def test_validate_wheel_set_missing_arch_raises(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    # Only x86_64, missing aarch64.
    whl = tmp_path / "agentbeacon-0.1.0-py3-none-manylinux_2_17_x86_64.whl"
    whl.write_bytes(b"PK\x03\x04")
    with pytest.raises(FileNotFoundError, match="Missing wheels.*aarch64"):
        validate_wheel_set([whl])


def test_validate_wheel_set_duplicate_arch_raises(tmp_path):
    wheels = _make_stub_wheels(tmp_path)
    # Add a second x86_64 wheel (stale build artifact).
    stale = (
        tmp_path
        / "agentbeacon-0.1.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    )
    stale.write_bytes(b"PK\x03\x04")
    with pytest.raises(FileNotFoundError, match="Multiple wheels.*x86_64"):
        validate_wheel_set(wheels + [stale])


def test_validate_wheel_set_unexpected_wheel_raises(tmp_path):
    wheels = _make_stub_wheels(tmp_path)
    # Add wheel with an unrecognized architecture.
    extra = tmp_path / "agentbeacon-0.1.0-py3-none-win_amd64.whl"
    extra.write_bytes(b"PK\x03\x04")
    with pytest.raises(FileNotFoundError, match="Unexpected wheels"):
        validate_wheel_set(wheels + [extra])


def test_validate_wheel_set_rejects_wrong_platform_same_arch(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    # macOS wheel with x86_64 — should NOT satisfy the manylinux_2_17_x86_64 slot.
    macos = tmp_path / "agentbeacon-0.1.0-py3-none-macosx_11_0_x86_64.whl"
    macos.write_bytes(b"PK\x03\x04")
    linux_arm = tmp_path / "agentbeacon-0.1.0-py3-none-manylinux_2_17_aarch64.whl"
    linux_arm.write_bytes(b"PK\x03\x04")
    with pytest.raises(
        FileNotFoundError, match="Missing wheels.*manylinux_2_17_x86_64"
    ):
        validate_wheel_set([macos, linux_arm])


# ---------------------------------------------------------------------------
# npm package discovery
# ---------------------------------------------------------------------------


def test_find_npm_packages_discovers_correct_order(tmp_path):
    _make_stub_npm_packages(tmp_path)
    platform_dirs, wrapper_dir = find_npm_packages(tmp_path)
    assert len(platform_dirs) >= 1
    assert wrapper_dir.name == PACKAGE_NAME
    # Platform dirs come first (they're returned separately for publish ordering).
    for d in platform_dirs:
        assert "cli-" in d.name


def test_find_npm_packages_missing_wrapper_raises(tmp_path):
    # Only create platform packages, no wrapper.
    pkg_dir = tmp_path / NPM_SCOPE / "cli-linux-x64"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text('{"name": "stub"}')
    with pytest.raises(FileNotFoundError, match="Wrapper package not found"):
        find_npm_packages(tmp_path)


def test_find_npm_packages_missing_platform_raises(tmp_path):
    # Only create wrapper, no platform packages.
    wrapper_dir = tmp_path / PACKAGE_NAME
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "package.json").write_text('{"name": "agentbeacon"}')
    with pytest.raises(FileNotFoundError, match="No platform packages"):
        find_npm_packages(tmp_path)


# ---------------------------------------------------------------------------
# npm platform set validation
# ---------------------------------------------------------------------------


def test_validate_npm_platform_set_complete(tmp_path):
    _make_stub_npm_packages(tmp_path)
    platform_dirs, _ = find_npm_packages(tmp_path)
    validate_npm_platform_set(platform_dirs)


def test_validate_npm_platform_set_missing_suffix_raises(tmp_path):
    # Only linux-x64, missing linux-arm64.
    pkg_dir = tmp_path / NPM_SCOPE / "cli-linux-x64"
    pkg_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="Missing npm platform.*linux-arm64"):
        validate_npm_platform_set([pkg_dir])


def test_validate_npm_platform_set_unexpected_raises(tmp_path):
    _make_stub_npm_packages(tmp_path)
    platform_dirs, _ = find_npm_packages(tmp_path)
    # Add an unexpected platform directory.
    extra = tmp_path / NPM_SCOPE / "cli-darwin-x64"
    extra.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="Unexpected npm platform"):
        validate_npm_platform_set(list(platform_dirs) + [extra])


# ---------------------------------------------------------------------------
# npm version validation
# ---------------------------------------------------------------------------


def test_validate_npm_versions_correct(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    platform_dirs, wrapper_dir = find_npm_packages(tmp_path)
    validate_npm_versions(platform_dirs, wrapper_dir, "1.2.3")


def test_validate_npm_versions_mismatch_raises(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    # Overwrite one platform package with a stale version.
    stale = tmp_path / NPM_SCOPE / "cli-linux-x64" / "package.json"
    stale.write_text('{"name": "@agentbeacon/cli-linux-x64", "version": "1.2.2"}')
    platform_dirs, wrapper_dir = find_npm_packages(tmp_path)
    with pytest.raises(ValueError, match="version mismatch.*cli-linux-x64.*1.2.2"):
        validate_npm_versions(platform_dirs, wrapper_dir, "1.2.3")


def test_validate_npm_versions_wrong_name_raises(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.0.0")
    # Overwrite a platform package with wrong name.
    wrong = tmp_path / NPM_SCOPE / "cli-linux-x64" / "package.json"
    wrong.write_text('{"name": "wrong-package", "version": "1.0.0"}')
    platform_dirs, wrapper_dir = find_npm_packages(tmp_path)
    with pytest.raises(ValueError, match="name mismatch.*cli-linux-x64.*wrong-package"):
        validate_npm_versions(platform_dirs, wrapper_dir, "1.0.0")


# ---------------------------------------------------------------------------
# Tool checks
# ---------------------------------------------------------------------------


def test_require_tool_python():
    path = require_tool("python3")
    assert path.is_file()


def test_require_tool_nonexistent_raises():
    with pytest.raises(FileNotFoundError, match="not found on PATH"):
        require_tool("nonexistent-tool-xyz-999")


# ---------------------------------------------------------------------------
# run_cmd dry-run
# ---------------------------------------------------------------------------


def test_run_cmd_dry_run_does_not_execute():
    # "false" would fail if actually executed.
    result = run_cmd(["false"], dry_run=True)
    assert result.returncode == 0
