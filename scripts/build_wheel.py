"""Generate platform-specific PyPI wheels from pre-built AgentBeacon binaries.

Usage:
    python scripts/build_wheel.py --target x86_64-unknown-linux-musl
    python scripts/build_wheel.py --target aarch64-unknown-linux-musl --output-dir dist/

Follows the zig-pypi / ruff pattern: raw ELF binaries placed in .data/scripts/
so pip copies them directly to <venv>/bin/. Thin Python wrapper in agentbeacon/.

Stdlib only -- no third-party dependencies.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import zipfile
from pathlib import Path

PACKAGE_NAME = "agentbeacon"
BINARIES = ["agentbeacon", "agentbeacon-worker"]
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

PLATFORM_MAP: dict[str, list[str]] = {
    "x86_64-unknown-linux-musl": [
        "manylinux_2_17_x86_64",
        "manylinux2014_x86_64",
        "musllinux_1_1_x86_64",
    ],
    "aarch64-unknown-linux-musl": [
        "manylinux_2_17_aarch64",
        "manylinux2014_aarch64",
        "musllinux_1_1_aarch64",
    ],
}

# Wheel wrapper files -- read from python/agentbeacon/ at build time.
WRAPPER_FILES = [
    "__init__.py",
    "__main__.py",
    "_find_binary.py",
]


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def semver_to_pep440(version: str) -> str:
    """Convert a SemVer pre-release version to PEP 440.

    Stable versions pass through unchanged.
    Pre-release: 1.0.0-alpha.1 -> 1.0.0a1, -beta.N -> bN, -rc.N -> rcN.
    """
    if "-" not in version:
        return version

    base, pre = version.split("-", 1)
    mapping = {"alpha": "a", "beta": "b", "rc": "rc"}

    for label, pep440_label in mapping.items():
        match = re.match(rf"^{label}\.(\d+)$", pre)
        if match:
            return f"{base}{pep440_label}{match.group(1)}"

    raise ValueError(
        f"Unknown pre-release identifier in '{version}'. Supported: alpha, beta, rc."
    )


def extract_version(cargo_toml: Path) -> str:
    """Extract version from [workspace.package] in Cargo.toml.

    Uses regex -- no TOML library needed (works on Python 3.10+).
    """
    text = cargo_toml.read_text()

    # Find the [workspace.package] section and extract version within it.
    ws_match = re.search(
        r"^\[workspace\.package\]\s*\n((?:(?!\n\[).+(?:\n|$))*)",
        text,
        re.MULTILINE,
    )
    if not ws_match:
        raise ValueError(f"No [workspace.package] section found in {cargo_toml}")

    section = ws_match.group(1)
    ver_match = re.search(r'^version\s*=\s*"([^"]+)"', section, re.MULTILINE)
    if not ver_match:
        raise ValueError(f"No version field in [workspace.package] in {cargo_toml}")

    return semver_to_pep440(ver_match.group(1))


# ---------------------------------------------------------------------------
# Platform tags
# ---------------------------------------------------------------------------


def platform_tags(target: str) -> list[str]:
    """Return the platform tag list for a Rust target triple."""
    tags = PLATFORM_MAP.get(target)
    if tags is None:
        supported = ", ".join(sorted(PLATFORM_MAP))
        raise ValueError(f"Unknown target '{target}'. Supported: {supported}")
    return tags


# ---------------------------------------------------------------------------
# Wheel filename + metadata
# ---------------------------------------------------------------------------


def wheel_filename(name: str, version: str, tags: list[str]) -> str:
    """PEP 427 wheel filename: {name}-{ver}-{pytag}-{abitag}-{platform1.platform2}.whl."""
    platform_str = ".".join(tags)
    return f"{name}-{version}-py3-none-{platform_str}.whl"


def generate_metadata(name: str, version: str) -> str:
    """PEP 566 METADATA content."""
    return (
        f"Metadata-Version: 2.1\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        f"Summary: Multi-agent orchestrator for AI coding tools\n"
        f"License: Apache-2.0\n"
        f"Requires-Python: >=3.10\n"
        f"Classifier: License :: OSI Approved :: Apache Software License\n"
        f"Classifier: Operating System :: POSIX :: Linux\n"
        f"Project-URL: Homepage, https://github.com/adrq/agentbeacon\n"
        f"Project-URL: Repository, https://github.com/adrq/agentbeacon\n"
    )


def generate_wheel_info(tags: list[str]) -> str:
    """WHEEL file content with Tag entries and Root-Is-Purelib: false."""
    lines = [
        "Wheel-Version: 1.0",
        "Generator: agentbeacon-build-wheel",
        "Root-Is-Purelib: false",
    ]
    for tag in tags:
        lines.append(f"Tag: py3-none-{tag}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# RECORD helpers
# ---------------------------------------------------------------------------


def _sha256_b64(data: bytes) -> str:
    """URL-safe base64-encoded SHA256 digest with no padding."""
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def record_entry(path: str, data: bytes) -> str:
    """Format a RECORD entry: path,sha256=<hash>,<size>."""
    return f"{path},sha256={_sha256_b64(data)},{len(data)}"


# ---------------------------------------------------------------------------
# Reproducible ZIP
# ---------------------------------------------------------------------------


class ReproducibleZipFile(zipfile.ZipFile):
    """ZipFile subclass that forces reproducible timestamps and permissions."""

    def write_file(
        self, arcname: str, data: bytes, *, executable: bool = False
    ) -> None:
        info = zipfile.ZipInfo(filename=arcname, date_time=ZIP_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        mode = 0o100755 if executable else 0o100644
        info.external_attr = mode << 16
        self.writestr(info, data)


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------


def build_wheel(
    target: str,
    binary_dir: Path,
    python_pkg_dir: Path,
    output_dir: Path,
    cargo_toml: Path,
) -> Path:
    """Assemble a platform wheel from pre-built binaries.

    Returns the path to the generated .whl file.
    """
    # Validate binaries exist.
    for name in BINARIES:
        path = binary_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"Binary not found: {path}. "
                f"Build with: make build-musl-x64 or make build-musl-arm64"
            )

    # Validate wrapper files exist.
    for fname in WRAPPER_FILES:
        path = python_pkg_dir / fname
        if not path.is_file():
            raise FileNotFoundError(f"Wrapper file not found: {path}")

    version = extract_version(cargo_toml)
    tags = platform_tags(target)
    whl_name = wheel_filename(PACKAGE_NAME, version, tags)

    dist_info = f"{PACKAGE_NAME}-{version}.dist-info"
    data_dir = f"{PACKAGE_NAME}-{version}.data"

    os.makedirs(output_dir, exist_ok=True)
    whl_path = output_dir / whl_name

    records: list[str] = []

    with ReproducibleZipFile(whl_path, "w") as zf:
        # 1. Python wrapper files -> agentbeacon/
        for fname in WRAPPER_FILES:
            arcname = f"{PACKAGE_NAME}/{fname}"
            file_data = (python_pkg_dir / fname).read_bytes()
            zf.write_file(arcname, file_data)
            records.append(record_entry(arcname, file_data))

        # 2. Binaries -> .data/scripts/
        for name in BINARIES:
            arcname = f"{data_dir}/scripts/{name}"
            file_data = (binary_dir / name).read_bytes()
            zf.write_file(arcname, file_data, executable=True)
            records.append(record_entry(arcname, file_data))

        # 3. LICENSE + NOTICE -> dist-info/licenses/
        # Both required: Apache 2.0 mandates NOTICE distribution.
        license_dir = cargo_toml.parent
        for license_file in ("LICENSE", "NOTICE"):
            src = license_dir / license_file
            if not src.is_file():
                raise FileNotFoundError(f"Required license file not found: {src}")
            arcname = f"{dist_info}/licenses/{license_file}"
            file_data = src.read_bytes()
            zf.write_file(arcname, file_data)
            records.append(record_entry(arcname, file_data))

        # 4. METADATA
        metadata_content = generate_metadata(PACKAGE_NAME, version).encode()
        arcname = f"{dist_info}/METADATA"
        zf.write_file(arcname, metadata_content)
        records.append(record_entry(arcname, metadata_content))

        # 5. WHEEL
        wheel_content = generate_wheel_info(tags).encode()
        arcname = f"{dist_info}/WHEEL"
        zf.write_file(arcname, wheel_content)
        records.append(record_entry(arcname, wheel_content))

        # 6. RECORD (self-entry has empty hash)
        records.append(f"{dist_info}/RECORD,,")
        record_content = "\n".join(records) + "\n"
        arcname = f"{dist_info}/RECORD"
        zf.write_file(arcname, record_content.encode())

    return whl_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate a platform wheel from pre-built AgentBeacon binaries."
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=sorted(PLATFORM_MAP),
        help="Rust target triple",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Directory for the generated .whl (default: dist/)",
    )
    parser.add_argument(
        "--binary-dir",
        type=Path,
        default=None,
        help="Directory containing pre-built binaries (default: target/<target>/release/)",
    )
    parser.add_argument(
        "--python-pkg-dir",
        type=Path,
        default=None,
        help="Directory containing the Python wrapper package (default: python/agentbeacon/)",
    )
    parser.add_argument(
        "--cargo-toml",
        type=Path,
        default=Path("Cargo.toml"),
        help="Path to workspace Cargo.toml (default: Cargo.toml)",
    )

    args = parser.parse_args(argv)

    if args.binary_dir is None:
        args.binary_dir = Path("target") / args.target / "release"
    if args.python_pkg_dir is None:
        args.python_pkg_dir = Path("python") / PACKAGE_NAME

    whl_path = build_wheel(
        target=args.target,
        binary_dir=args.binary_dir,
        python_pkg_dir=args.python_pkg_dir,
        output_dir=args.output_dir,
        cargo_toml=args.cargo_toml,
    )

    print(f"Built wheel: {whl_path}")
    print(f"  Size: {whl_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
