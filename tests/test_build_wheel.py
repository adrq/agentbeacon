"""Unit tests for scripts/build_wheel.py -- all pure functions."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Import build_wheel.py from scripts/ (not a package).
_spec = importlib.util.spec_from_file_location(
    "build_wheel",
    Path(__file__).resolve().parent.parent / "scripts" / "build_wheel.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

semver_to_pep440 = _mod.semver_to_pep440
extract_version = _mod.extract_version
platform_tags = _mod.platform_tags
wheel_filename = _mod.wheel_filename
generate_metadata = _mod.generate_metadata
generate_wheel_info = _mod.generate_wheel_info
record_entry = _mod.record_entry
build_wheel = _mod.build_wheel
BINARIES = _mod.BINARIES
WRAPPER_FILES = _mod.WRAPPER_FILES


# ---------------------------------------------------------------------------
# Version conversion
# ---------------------------------------------------------------------------


def test_semver_to_pep440_stable():
    assert semver_to_pep440("1.0.0") == "1.0.0"


def test_semver_to_pep440_alpha():
    assert semver_to_pep440("1.0.0-alpha.1") == "1.0.0a1"


def test_semver_to_pep440_beta():
    assert semver_to_pep440("2.1.0-beta.3") == "2.1.0b3"


def test_semver_to_pep440_rc():
    assert semver_to_pep440("0.5.0-rc.1") == "0.5.0rc1"


def test_semver_to_pep440_unknown_raises():
    with pytest.raises(ValueError, match="Unknown pre-release"):
        semver_to_pep440("1.0.0-gamma.1")


# ---------------------------------------------------------------------------
# Version extraction
# ---------------------------------------------------------------------------


def test_extract_version():
    cargo_toml = Path(__file__).resolve().parent.parent / "Cargo.toml"
    assert extract_version(cargo_toml) == "0.1.0"


def test_extract_version_missing_workspace_raises(tmp_path):
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[package]\nversion = "1.0.0"\n')
    with pytest.raises(ValueError, match="No \\[workspace\\.package\\]"):
        extract_version(cargo)


# ---------------------------------------------------------------------------
# Platform tags
# ---------------------------------------------------------------------------


def test_platform_tags_x86_64():
    tags = platform_tags("x86_64-unknown-linux-musl")
    assert tags == [
        "manylinux_2_17_x86_64",
        "manylinux2014_x86_64",
        "musllinux_1_1_x86_64",
    ]


def test_platform_tags_aarch64():
    tags = platform_tags("aarch64-unknown-linux-musl")
    assert tags == [
        "manylinux_2_17_aarch64",
        "manylinux2014_aarch64",
        "musllinux_1_1_aarch64",
    ]


def test_platform_tags_unknown_raises():
    with pytest.raises(ValueError, match="Unknown target"):
        platform_tags("powerpc-unknown-linux-musl")


# ---------------------------------------------------------------------------
# Wheel filename
# ---------------------------------------------------------------------------


def test_wheel_filename_format():
    tags = ["manylinux_2_17_x86_64", "manylinux2014_x86_64", "musllinux_1_1_x86_64"]
    name = wheel_filename("agentbeacon", "0.1.0", tags)
    assert name == (
        "agentbeacon-0.1.0-py3-none-"
        "manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64.whl"
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_generate_metadata_fields():
    meta = generate_metadata("agentbeacon", "0.1.0")
    assert "Metadata-Version: 2.1" in meta
    assert "Name: agentbeacon" in meta
    assert "Version: 0.1.0" in meta
    assert "Requires-Python: >=3.10" in meta
    assert "License: Apache-2.0" in meta


# ---------------------------------------------------------------------------
# WHEEL info
# ---------------------------------------------------------------------------


def test_generate_wheel_info_tags():
    tags = ["manylinux_2_17_x86_64", "manylinux2014_x86_64"]
    info = generate_wheel_info(tags)
    assert "Tag: py3-none-manylinux_2_17_x86_64" in info
    assert "Tag: py3-none-manylinux2014_x86_64" in info


def test_generate_wheel_info_purelib_false():
    info = generate_wheel_info(["manylinux_2_17_x86_64"])
    assert "Root-Is-Purelib: false" in info


# ---------------------------------------------------------------------------
# RECORD
# ---------------------------------------------------------------------------


def test_record_entry_hash():
    data = b"hello world"
    entry = record_entry("test.txt", data)
    assert entry == "test.txt,sha256=uU0nuZNNPgilLlLX2n2r-sSE7-N6U4DukIj3rOLvzek,11"


def test_record_entry_no_padding():
    # base64 of SHA256 can produce padding, verify it's stripped.
    data = b"test data for padding check"
    entry = record_entry("f.txt", data)
    hash_part = entry.split(",")[1].split("=", 1)[1]
    assert "=" not in hash_part


# ---------------------------------------------------------------------------
# Build wheel error cases
# ---------------------------------------------------------------------------


def test_build_wheel_missing_binary_raises(tmp_path):
    # Empty binary dir -> FileNotFoundError
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    for f in WRAPPER_FILES:
        (pkg_dir / f).write_text("# stub")

    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[workspace.package]\nversion = "1.0.0"\n')

    with pytest.raises(FileNotFoundError, match="Binary not found"):
        build_wheel(
            target="x86_64-unknown-linux-musl",
            binary_dir=binary_dir,
            python_pkg_dir=pkg_dir,
            output_dir=tmp_path / "dist",
            cargo_toml=cargo,
        )


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_wheel_reproducible(tmp_path):
    """Building the same wheel twice produces identical bytes."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    for name in BINARIES:
        (binary_dir / name).write_bytes(b"\x7fELF" + b"\x00" * 100)

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    for f in WRAPPER_FILES:
        (pkg_dir / f).write_text("# wrapper stub")

    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[workspace.package]\nversion = "1.0.0"\n')

    # License files required by build_wheel.
    (tmp_path / "LICENSE").write_text("Apache License 2.0")
    (tmp_path / "NOTICE").write_text("Test notice")

    out1 = tmp_path / "dist1"
    out2 = tmp_path / "dist2"

    whl1 = build_wheel(
        target="x86_64-unknown-linux-musl",
        binary_dir=binary_dir,
        python_pkg_dir=pkg_dir,
        output_dir=out1,
        cargo_toml=cargo,
    )
    whl2 = build_wheel(
        target="x86_64-unknown-linux-musl",
        binary_dir=binary_dir,
        python_pkg_dir=pkg_dir,
        output_dir=out2,
        cargo_toml=cargo,
    )

    assert whl1.read_bytes() == whl2.read_bytes()
