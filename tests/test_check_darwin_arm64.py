# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for scripts/check_darwin_arm64.sh and the darwin package targets."""

from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BASE_DIR / "scripts" / "check_darwin_arm64.sh"

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
CPU_X86_64 = 0x01000007
CPU_ARM64 = 0x0100000C
MH_EXECUTE = 2
MH_DYLIB = 6

STAGE_HINT = (
    "Stage the signed Mac binaries from build-macos-native in "
    "target/aarch64-apple-darwin/release/ first."
)


def _thin(cputype: int, filetype: int = MH_EXECUTE) -> bytes:
    cpusubtype = 3 if cputype == CPU_X86_64 else 0
    header = struct.pack(
        "<IiiIIIII", MH_MAGIC_64, cputype, cpusubtype, filetype, 0, 0, 0, 0
    )
    return header.ljust(64, b"\x00")


def _universal(cputypes: list[int]) -> bytes:
    align = 4096
    head = struct.pack(">II", FAT_MAGIC, len(cputypes))
    slices = b""
    for i, cputype in enumerate(cputypes):
        offset = align * (i + 1)
        cpusubtype = 3 if cputype == CPU_X86_64 else 0
        head += struct.pack(">iiIII", cputype, cpusubtype, offset, 64, 12)
        slices += _thin(cputype).ljust(align, b"\x00")
    return head.ljust(align, b"\x00") + slices


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    path.chmod(0o755)
    return path


def _check(*paths: Path, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *map(str, paths)], capture_output=True, text=True, env=env
    )


def _env_with_file_stub(tmp_path: Path, output: str) -> dict:
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    (tmp_path / "file-output").write_text(output)
    stub = stub_dir / "file"
    stub.write_text(f"#!/bin/sh\ncat '{tmp_path / 'file-output'}'\n")
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    return env


def test_thin_arm64_executable_passes(tmp_path):
    a = _write(tmp_path, "agentbeacon", _thin(CPU_ARM64))
    b = _write(tmp_path, "agentbeacon-worker", _thin(CPU_ARM64))
    result = _check(a, b)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    "data",
    [
        _thin(CPU_X86_64),
        _thin(CPU_ARM64, MH_DYLIB),
        _universal([CPU_X86_64, CPU_ARM64]),
        _universal([CPU_ARM64]),
        b"\x7fELF\x02\x01\x01" + b"\x00" * 57,
    ],
    ids=["x86_64", "arm64-dylib", "universal", "universal-arm64-only", "elf"],
)
def test_non_arm64_executable_fails(tmp_path, data):
    good = _write(tmp_path, "agentbeacon", _thin(CPU_ARM64))
    bad = _write(tmp_path, "agentbeacon-worker", data)
    result = _check(good, bad)
    assert result.returncode == 1
    assert result.stderr.startswith(
        f"ERROR: {bad}: not an arm64 Mach-O executable (file: "
    )
    assert result.stderr.splitlines()[-1] == STAGE_HINT
    assert f"ERROR: {good}:" not in result.stderr


def test_missing_binary_fails(tmp_path):
    missing = tmp_path / "agentbeacon"
    result = _check(missing)
    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        f"ERROR: {missing}: not an arm64 Mach-O executable (file: no such file).",
        STAGE_HINT,
    ]


@pytest.mark.parametrize(
    "target, build_cmd",
    [
        (
            "package-wheel-darwin-arm64",
            "uv run python scripts/build_wheel.py --target aarch64-apple-darwin "
            "--output-dir dist/",
        ),
        (
            "package-npm-darwin-arm64",
            "uv run python scripts/build_npm.py platform --target aarch64-apple-darwin "
            "--output-dir dist/npm/",
        ),
    ],
)
def test_package_targets_check_binaries_before_packaging(target, build_cmd):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")
    }
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "scripts/check_darwin_arm64.sh "
        "target/aarch64-apple-darwin/release/agentbeacon "
        "target/aarch64-apple-darwin/release/agentbeacon-worker",
        build_cmd,
    ]


@pytest.mark.parametrize(
    "output, ok",
    [
        ("Mach-O 64-bit executable arm64\n", True),
        (
            "Mach-O 64-bit arm64 executable, flags:<NOUNDEFS|DYLDLINK|TWOLEVEL|PIE>\n",
            True,
        ),
        ("Mach-O 64-bit executable arm64e\n", False),
        ("Mach-O 64-bit executable x86_64\n", False),
        (
            "Mach-O universal binary with 2 architectures: "
            "[x86_64:Mach-O 64-bit executable x86_64] [arm64]\n"
            "Mach-O 64-bit executable x86_64\n"
            "Mach-O 64-bit executable arm64\n",
            False,
        ),
    ],
    ids=["macos-thin", "libmagic-thin", "arm64e", "macos-x86_64", "macos-universal"],
)
def test_file_output_formats(tmp_path, output, ok):
    binary = _write(tmp_path, "agentbeacon", b"stub")
    result = _check(binary, env=_env_with_file_stub(tmp_path, output))
    assert result.returncode == (0 if ok else 1), result.stderr
    if not ok:
        assert result.stderr.splitlines()[-1] == STAGE_HINT
