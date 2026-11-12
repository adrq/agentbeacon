# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for scripts/publish.py -- all pure functions."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
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

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, _scripts_dir / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_build_wheel = _load_script("build_wheel")
_build_npm = _load_script("build_npm")

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
validate_npm_wrapper_dependencies = _mod.validate_npm_wrapper_dependencies
publish_npm = _mod.publish_npm
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
        "agentbeacon-0.1.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64.whl",
        "agentbeacon-0.1.0-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl",
        "agentbeacon-0.1.0-py3-none-macosx_11_0_arm64.whl",
    ]
    paths = []
    for name in names:
        p = dist_dir / name
        p.write_bytes(b"PK\x03\x04")
        paths.append(p)
    return paths


def _wrapper_deps(version: str) -> dict[str, str]:
    return {f"{NPM_SCOPE}/cli-{s}": version for s in TARGET_TO_NPM_SUFFIX.values()}


def _write_wrapper(dist_npm_dir: Path, version: str, deps: dict[str, str]) -> None:
    wrapper_dir = dist_npm_dir / PACKAGE_NAME
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    (wrapper_dir / "package.json").write_text(
        json.dumps(
            {"name": PACKAGE_NAME, "version": version, "optionalDependencies": deps}
        )
    )


def _make_stub_npm_packages(dist_npm_dir: Path, version: str = "0.1.0") -> None:
    for suffix in TARGET_TO_NPM_SUFFIX.values():
        pkg_dir = dist_npm_dir / NPM_SCOPE / f"cli-{suffix}"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        name = f"{NPM_SCOPE}/cli-{suffix}"
        (pkg_dir / "package.json").write_text(
            f'{{"name": "{name}", "version": "{version}"}}'
        )
    _write_wrapper(dist_npm_dir, version, _wrapper_deps(version))


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
    whl = (
        tmp_path
        / "agentbeacon-1.0.0a1-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64.whl"
    )
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
    assert len(names) == len(set(names)), "Tarball names are not distinct per target"


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
    assert len(found) == 3
    assert found == sorted(wheels)


def test_find_wheels_empty_dir_raises(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(FileNotFoundError, match="No .whl files"):
        find_wheels(tmp_path, "0.1.0")


def test_find_wheels_ignores_other_versions(tmp_path):
    _make_stub_wheels(tmp_path)
    # Add a wheel for a different version.
    stale = (
        tmp_path
        / "agentbeacon-0.0.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64.whl"
    )
    stale.write_bytes(b"PK\x03\x04")
    found = find_wheels(tmp_path, "0.1.0")
    assert len(found) == 3
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
    whl = (
        tmp_path
        / "agentbeacon-0.1.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64.whl"
    )
    whl.write_bytes(b"PK\x03\x04")
    with pytest.raises(FileNotFoundError, match="Missing wheels.*aarch64"):
        validate_wheel_set([whl])


def test_validate_wheel_set_missing_darwin_raises(tmp_path):
    wheels = [w for w in _make_stub_wheels(tmp_path) if "macosx" not in w.name]
    with pytest.raises(
        FileNotFoundError,
        match=r"^Missing wheels for platforms: macosx_11_0_arm64\. "
        r"Build with: make build-wheels package-wheel-darwin-arm64$",
    ):
        validate_wheel_set(wheels)


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


def test_validate_npm_platform_set_missing_darwin_raises(tmp_path):
    _make_stub_npm_packages(tmp_path)
    platform_dirs, _ = find_npm_packages(tmp_path)
    linux_only = [d for d in platform_dirs if d.name != "cli-darwin-arm64"]
    assert len(linux_only) == 2
    with pytest.raises(
        FileNotFoundError,
        match=r"^Missing npm platform packages: cli-darwin-arm64\. "
        r"Build with: make build-npm-x64 build-npm-arm64 package-npm-darwin-arm64$",
    ):
        validate_npm_platform_set(linux_only)


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
# npm wrapper dependency validation
# ---------------------------------------------------------------------------


def test_validate_npm_wrapper_dependencies_correct(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


def test_validate_npm_wrapper_dependencies_missing_raises(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    deps = _wrapper_deps("1.2.3")
    del deps[f"{NPM_SCOPE}/cli-linux-arm64"]
    _write_wrapper(tmp_path, "1.2.3", deps)
    with pytest.raises(
        ValueError,
        match=r"optionalDependencies mismatch \(expected 1\.2\.3\): "
        r"missing @agentbeacon/cli-linux-arm64\. Rebuild",
    ):
        validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


def test_validate_npm_wrapper_dependencies_absent_field_raises(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    (tmp_path / PACKAGE_NAME / "package.json").write_text(
        '{"name": "agentbeacon", "version": "1.2.3"}'
    )
    with pytest.raises(
        ValueError,
        match=r"missing @agentbeacon/cli-darwin-arm64, @agentbeacon/cli-linux-arm64, "
        r"@agentbeacon/cli-linux-x64\.",
    ):
        validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


def test_validate_npm_wrapper_dependencies_null_treated_as_empty(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    (tmp_path / PACKAGE_NAME / "package.json").write_text(
        '{"name": "agentbeacon", "version": "1.2.3", "optionalDependencies": null}'
    )
    with pytest.raises(
        ValueError,
        match=r"missing @agentbeacon/cli-darwin-arm64, @agentbeacon/cli-linux-arm64, "
        r"@agentbeacon/cli-linux-x64\.",
    ):
        validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


@pytest.mark.parametrize(
    "value, shown",
    [
        ("0", "0"),
        ("7", "7"),
        ("[]", r"\[\]"),
        ('["@agentbeacon/cli-linux-x64"]', r"\["),
    ],
    ids=["zero", "number", "empty-array", "array"],
)
def test_validate_npm_wrapper_dependencies_non_object_raises(tmp_path, value, shown):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    (tmp_path / PACKAGE_NAME / "package.json").write_text(
        f'{{"name": "agentbeacon", "version": "1.2.3", "optionalDependencies": {value}}}'
    )
    with pytest.raises(
        ValueError,
        match=rf"^npm wrapper optionalDependencies is not an object: {shown}.*\. "
        r"Rebuild with: make build-npm-wrapper$",
    ):
        validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


def test_validate_npm_wrapper_dependencies_extra_raises(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    deps = _wrapper_deps("1.2.3")
    deps[f"{NPM_SCOPE}/cli-win32-x64"] = "1.2.3"
    _write_wrapper(tmp_path, "1.2.3", deps)
    with pytest.raises(
        ValueError, match=r": unexpected @agentbeacon/cli-win32-x64\. Rebuild"
    ):
        validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


def test_validate_npm_wrapper_dependencies_wrong_version_raises(tmp_path):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    deps = _wrapper_deps("1.2.3")
    deps[f"{NPM_SCOPE}/cli-linux-x64"] = "1.2.2"
    _write_wrapper(tmp_path, "1.2.3", deps)
    with pytest.raises(
        ValueError, match=r": wrong version @agentbeacon/cli-linux-x64@1\.2\.2\. "
    ):
        validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


def test_publish_npm_rejects_bad_wrapper_before_publishing(tmp_path, monkeypatch):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    _write_wrapper(tmp_path, "1.2.3", {})
    registry = _FakeRegistry(monkeypatch, published=set())
    with pytest.raises(ValueError, match="optionalDependencies mismatch"):
        publish_npm(tmp_path, "1.2.3")
    assert registry.calls == []


# ---------------------------------------------------------------------------
# npm publish and --resume
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """Stands in for the npm CLI: answers `npm view` from a set of
    published `name@version` strings and records every command."""

    def __init__(self, monkeypatch, published, view_result=None):
        self.published = published
        self.view_result = view_result
        self.calls: list[list[str]] = []
        self.sleeps: list[float] = []
        monkeypatch.setattr(_mod.subprocess, "run", self.run)
        monkeypatch.setattr(_mod, "require_tool", lambda name: Path("/usr/bin") / name)
        monkeypatch.setattr(_mod.time, "sleep", self.sleeps.append)
        monkeypatch.delenv("NPM_TOKEN", raising=False)

    def run(self, cmd, check=False, capture_output=False, text=False):
        self.calls.append(cmd)
        if cmd[:2] != ["npm", "view"]:
            return subprocess.CompletedProcess(cmd, 0)
        if self.view_result is not None:
            returncode, stdout, stderr = self.view_result
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        spec = cmd[2]
        name, version = spec.rsplit("@", 1)
        if spec in self.published:
            return subprocess.CompletedProcess(cmd, 0, f'"{version}"\n', "")
        if any(p.rsplit("@", 1)[0] == name for p in self.published):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(
            cmd,
            1,
            "",
            f"npm error code E404\nnpm error 404 '{spec}' is not in this registry.\n",
        )

    def views(self) -> list[str]:
        return [c[2] for c in self.calls if c[:2] == ["npm", "view"]]

    def publishes(self) -> list[str]:
        return [Path(c[2]).name for c in self.calls if c[:2] == ["npm", "publish"]]


def test_publish_npm_without_resume_never_queries_registry(tmp_path, monkeypatch):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    registry = _FakeRegistry(monkeypatch, published={"agentbeacon@1.2.3"})
    publish_npm(tmp_path, "1.2.3")
    assert registry.views() == []
    assert registry.publishes() == [
        "cli-darwin-arm64",
        "cli-linux-arm64",
        "cli-linux-x64",
        PACKAGE_NAME,
    ]
    assert registry.sleeps == [10]


def test_publish_npm_resume_partial_platform_publish(tmp_path, monkeypatch):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    registry = _FakeRegistry(
        monkeypatch,
        published={"@agentbeacon/cli-linux-arm64@1.2.3", "agentbeacon@1.2.2"},
    )
    publish_npm(tmp_path, "1.2.3", resume=True)
    assert registry.views() == [
        "@agentbeacon/cli-darwin-arm64@1.2.3",
        "@agentbeacon/cli-linux-arm64@1.2.3",
        "@agentbeacon/cli-linux-x64@1.2.3",
        "agentbeacon@1.2.3",
    ]
    assert registry.publishes() == ["cli-darwin-arm64", "cli-linux-x64", PACKAGE_NAME]
    assert registry.sleeps == [10]
    assert registry.calls[-1] == [
        "npm",
        "publish",
        str(tmp_path / PACKAGE_NAME),
        "--access",
        "public",
    ]


def test_publish_npm_resume_only_wrapper_missing(tmp_path, monkeypatch):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    registry = _FakeRegistry(
        monkeypatch,
        published={
            "@agentbeacon/cli-darwin-arm64@1.2.3",
            "@agentbeacon/cli-linux-arm64@1.2.3",
            "@agentbeacon/cli-linux-x64@1.2.3",
        },
    )
    publish_npm(tmp_path, "1.2.3", resume=True)
    assert registry.publishes() == [PACKAGE_NAME]
    assert registry.sleeps == [10]


def test_publish_npm_resume_everything_published(tmp_path, monkeypatch, capsys):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    registry = _FakeRegistry(
        monkeypatch,
        published={
            "@agentbeacon/cli-darwin-arm64@1.2.3",
            "@agentbeacon/cli-linux-arm64@1.2.3",
            "@agentbeacon/cli-linux-x64@1.2.3",
            "agentbeacon@1.2.3",
        },
    )
    publish_npm(tmp_path, "1.2.3", resume=True)
    assert registry.publishes() == []
    assert registry.sleeps == []
    assert "All npm packages for 1.2.3 are already published" in capsys.readouterr().out


def test_publish_npm_resume_prerelease_dry_run_with_token(tmp_path, monkeypatch):
    _make_stub_npm_packages(tmp_path, version="1.0.0-rc.1")
    registry = _FakeRegistry(
        monkeypatch, published={"@agentbeacon/cli-darwin-arm64@1.0.0-rc.1"}
    )
    monkeypatch.setenv("NPM_TOKEN", "test-token")
    publish_npm(tmp_path, "1.0.0-rc.1", dry_run=True, resume=True)

    view = registry.calls[0]
    assert view[:5] == [
        "npm",
        "view",
        "@agentbeacon/cli-darwin-arm64@1.0.0-rc.1",
        "version",
        "--json",
    ]
    assert view[5] == "--userconfig"
    npmrc = view[6]
    assert registry.publishes() == ["cli-linux-arm64", "cli-linux-x64", PACKAGE_NAME]
    for cmd in registry.calls[4:]:
        assert cmd[3:] == [
            "--access",
            "public",
            "--userconfig",
            npmrc,
            "--tag",
            "next",
            "--dry-run",
        ]
    assert registry.sleeps == []
    assert not Path(npmrc).exists()


@pytest.mark.parametrize(
    "view_result, message",
    [
        ((1, "", "npm error code ENOTFOUND\n"), "npm view failed .*ENOTFOUND"),
        ((1, "", "npm error code E401\n"), "npm view failed .*E401"),
        ((0, '"9.9.9"\n', ""), "Unexpected npm view output .*9\\.9\\.9"),
        ((0, "not json\n", ""), "Unexpected npm view output .*not json"),
        ((0, '["1.2.3"]\n', ""), "Unexpected npm view output"),
    ],
    ids=["network-error", "auth-error", "other-version", "non-json", "json-array"],
)
def test_publish_npm_resume_registry_error_aborts(
    tmp_path, monkeypatch, view_result, message
):
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    registry = _FakeRegistry(monkeypatch, published=set(), view_result=view_result)
    with pytest.raises(RuntimeError, match=message):
        publish_npm(tmp_path, "1.2.3", resume=True)
    assert registry.publishes() == []


def test_main_npm_resume_flag(tmp_path, monkeypatch):
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[workspace.package]\nversion = "1.2.3"\n')
    _make_stub_npm_packages(tmp_path / "dist" / "npm", version="1.2.3")
    registry = _FakeRegistry(
        monkeypatch,
        published={
            "@agentbeacon/cli-darwin-arm64@1.2.3",
            "@agentbeacon/cli-linux-arm64@1.2.3",
            "@agentbeacon/cli-linux-x64@1.2.3",
        },
    )
    _mod.main(
        [
            "--cargo-toml",
            str(cargo),
            "--dist-dir",
            str(tmp_path / "dist"),
            "npm",
            "--resume",
        ]
    )
    assert registry.publishes() == [PACKAGE_NAME]


def test_publish_all_warns_github_is_linux_only(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(_mod, "publish_pypi", lambda *a, **k: calls.append("pypi"))
    monkeypatch.setattr(_mod, "publish_npm", lambda *a, **k: calls.append("npm"))
    monkeypatch.setattr(_mod, "publish_github", lambda *a, **k: calls.append("github"))
    _mod.publish_all("1.2.3", "v1.2.3", tmp_path, tmp_path / "npm", tmp_path)
    out = capsys.readouterr().out
    assert calls == ["pypi", "npm", "github"]
    assert out.splitlines()[0] == (
        "Warning: the GitHub release includes only the Linux binaries "
        "(x86_64-unknown-linux-musl, aarch64-unknown-linux-musl); "
        "macOS is published to PyPI and npm only."
    )


# ---------------------------------------------------------------------------
# Platform lists agree across build and publish scripts
# ---------------------------------------------------------------------------


def _resolve_js_platforms() -> dict[str, str]:
    resolve_js = _scripts_dir.parent / "npm" / "lib" / "resolve.js"
    script = (
        "const fs = require('fs'); const vm = require('vm');"
        "const src = fs.readFileSync(process.argv[1], 'utf8');"
        "const ctx = {module: {exports: {}}, require, process};"
        "process.stdout.write(JSON.stringify(vm.runInNewContext("
        "src + '\\n;PLATFORMS', ctx)));"
    )
    out = subprocess.run(
        ["node", "-e", script, str(resolve_js)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_platform_targets_agree():
    targets = set(_build_wheel.PLATFORM_MAP)
    assert set(_build_npm.TARGET_MAP) == targets
    assert set(TARGET_TO_NPM_SUFFIX) == targets
    assert set(TARGET_TO_WHEEL_TAG) == targets


def test_platform_npm_suffixes_agree():
    for target, info in _build_npm.TARGET_MAP.items():
        assert TARGET_TO_NPM_SUFFIX.get(target) == info["suffix"], target


def test_platform_wheel_tags_agree():
    for target, tag in TARGET_TO_WHEEL_TAG.items():
        assert tag in _build_wheel.PLATFORM_MAP.get(target, []), target


def test_platform_resolve_js_agrees():
    expected = {
        f"{info['os']}-{info['cpu']}": f"{NPM_SCOPE}/cli-{info['suffix']}"
        for info in _build_npm.TARGET_MAP.values()
    }
    assert _resolve_js_platforms() == expected


def test_platform_wrapper_dependencies_agree(tmp_path):
    generated = json.loads(_build_npm.generate_wrapper_package_json("1.2.3"))
    assert generated["optionalDependencies"] == _wrapper_deps("1.2.3")
    _make_stub_npm_packages(tmp_path, version="1.2.3")
    _write_wrapper(tmp_path, "1.2.3", generated["optionalDependencies"])
    validate_npm_wrapper_dependencies(tmp_path / PACKAGE_NAME, "1.2.3")


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
