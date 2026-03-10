"""Generate npm packages from pre-built AgentBeacon binaries.

Usage:
    python scripts/build_npm.py platform --target x86_64-unknown-linux-musl
    python scripts/build_npm.py wrapper [--npm-dir npm/]

Two subcommands:
  platform  -- generates a platform-specific package (@agentbeacon/cli-linux-x64)
  wrapper   -- generates the wrapper package (agentbeacon)

Stdlib only -- no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

PACKAGE_NAME = "agentbeacon"
PACKAGE_SCOPE = "@agentbeacon"
BINARIES = ["agentbeacon", "agentbeacon-worker"]

TARGET_MAP: dict[str, dict[str, str]] = {
    "x86_64-unknown-linux-musl": {
        "os": "linux",
        "cpu": "x64",
        "suffix": "linux-x64",
    },
    "aarch64-unknown-linux-musl": {
        "os": "linux",
        "cpu": "arm64",
        "suffix": "linux-arm64",
    },
}

WRAPPER_JS_FILES = [
    "bin/agentbeacon.js",
    "bin/worker.js",
    "lib/resolve.js",
]

LICENSE_FILES = ["LICENSE", "NOTICE"]


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def extract_version(cargo_toml: Path) -> str:
    """Extract raw SemVer version from [workspace.package] in Cargo.toml."""
    text = cargo_toml.read_text()

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

    return ver_match.group(1)


# ---------------------------------------------------------------------------
# Target helpers
# ---------------------------------------------------------------------------


def target_to_platform(target: str) -> dict[str, str]:
    """Lookup target triple in TARGET_MAP. Raises ValueError for unknown targets."""
    info = TARGET_MAP.get(target)
    if info is None:
        supported = ", ".join(sorted(TARGET_MAP))
        raise ValueError(f"Unknown target '{target}'. Supported: {supported}")
    return info


def platform_package_name(target: str) -> str:
    """Return the scoped npm package name for a target triple."""
    info = target_to_platform(target)
    return f"{PACKAGE_SCOPE}/cli-{info['suffix']}"


# ---------------------------------------------------------------------------
# package.json generation
# ---------------------------------------------------------------------------


def generate_platform_package_json(target: str, version: str) -> str:
    """Generate package.json content for a platform package."""
    info = target_to_platform(target)
    name = f"{PACKAGE_SCOPE}/cli-{info['suffix']}"

    data = {
        "name": name,
        "version": version,
        "description": f"AgentBeacon platform binaries for {info['os']} {info['cpu']}",
        "license": "Apache-2.0",
        "repository": {
            "type": "git",
            "url": "https://github.com/adrq/agentbeacon",
        },
        "os": [info["os"]],
        "cpu": [info["cpu"]],
        "preferUnplugged": True,
        "publishConfig": {"access": "public"},
        "files": [
            "bin/",
            "LICENSE",
            "NOTICE",
        ],
    }
    return json.dumps(data, indent=2) + "\n"


def generate_wrapper_package_json(version: str) -> str:
    """Generate package.json content for the wrapper package."""
    optional_deps = {}
    for target in sorted(TARGET_MAP):
        pkg_name = platform_package_name(target)
        optional_deps[pkg_name] = version

    data = {
        "name": PACKAGE_NAME,
        "version": version,
        "description": "Multi-agent orchestrator for AI coding tools",
        "license": "Apache-2.0",
        "repository": {
            "type": "git",
            "url": "https://github.com/adrq/agentbeacon",
        },
        "bin": {
            "agentbeacon": "bin/agentbeacon.js",
            "agentbeacon-worker": "bin/worker.js",
        },
        "files": [
            "bin/",
            "lib/",
            "LICENSE",
            "NOTICE",
        ],
        "publishConfig": {"access": "public"},
        "optionalDependencies": optional_deps,
    }
    return json.dumps(data, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Package assembly
# ---------------------------------------------------------------------------


def build_platform_package(
    target: str,
    binary_dir: Path,
    output_dir: Path,
    cargo_toml: Path,
    license_dir: Path,
) -> Path:
    """Assemble a platform npm package directory from pre-built binaries.

    Returns the path to the generated package directory.
    """
    # Validate binaries exist.
    for name in BINARIES:
        path = binary_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"Binary not found: {path}. "
                f"Build with: make build-musl-x64 or make build-musl-arm64"
            )

    # Validate license files exist.
    for fname in LICENSE_FILES:
        path = license_dir / fname
        if not path.is_file():
            raise FileNotFoundError(f"Required license file not found: {path}")

    version = extract_version(cargo_toml)
    info = target_to_platform(target)
    pkg_dir = output_dir / PACKAGE_SCOPE / f"cli-{info['suffix']}"

    # Clean stale output.
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)

    bin_dir = pkg_dir / "bin"
    os.makedirs(bin_dir, exist_ok=True)

    # Copy binaries and ensure executable regardless of source permissions.
    for name in BINARIES:
        dest = bin_dir / name
        shutil.copy2(binary_dir / name, dest)
        dest.chmod(dest.stat().st_mode | 0o755)

    # Write generated package.json.
    (pkg_dir / "package.json").write_text(
        generate_platform_package_json(target, version)
    )

    # Copy license files.
    for fname in LICENSE_FILES:
        shutil.copy2(license_dir / fname, pkg_dir / fname)

    return pkg_dir


def build_wrapper_package(
    npm_source_dir: Path,
    output_dir: Path,
    cargo_toml: Path,
    license_dir: Path,
) -> Path:
    """Assemble the wrapper npm package directory.

    Returns the path to the generated package directory.
    """
    # Validate JS source files exist.
    for rel_path in WRAPPER_JS_FILES:
        path = npm_source_dir / rel_path
        if not path.is_file():
            raise FileNotFoundError(f"JS source file not found: {path}")

    # Validate license files exist.
    for fname in LICENSE_FILES:
        path = license_dir / fname
        if not path.is_file():
            raise FileNotFoundError(f"Required license file not found: {path}")

    version = extract_version(cargo_toml)
    pkg_dir = output_dir / PACKAGE_NAME

    # Clean stale output.
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)

    os.makedirs(pkg_dir, exist_ok=True)

    # Copy JS source files (preserve directory structure).
    for rel_path in WRAPPER_JS_FILES:
        dest = pkg_dir / rel_path
        os.makedirs(dest.parent, exist_ok=True)
        shutil.copy2(npm_source_dir / rel_path, dest)

    # Ensure bin scripts are executable regardless of source permissions.
    for rel_path in WRAPPER_JS_FILES:
        if rel_path.startswith("bin/"):
            p = pkg_dir / rel_path
            p.chmod(p.stat().st_mode | 0o755)

    # Write generated package.json.
    (pkg_dir / "package.json").write_text(generate_wrapper_package_json(version))

    # Copy license files.
    for fname in LICENSE_FILES:
        shutil.copy2(license_dir / fname, pkg_dir / fname)

    return pkg_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate npm packages from pre-built AgentBeacon binaries."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- platform subcommand --
    p_platform = sub.add_parser(
        "platform", help="Generate a platform-specific npm package"
    )
    p_platform.add_argument(
        "--target",
        required=True,
        choices=sorted(TARGET_MAP),
        help="Rust target triple",
    )
    p_platform.add_argument(
        "--binary-dir",
        type=Path,
        default=None,
        help="Directory containing pre-built binaries (default: target/<target>/release/)",
    )
    p_platform.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist/npm"),
        help="Output directory (default: dist/npm/)",
    )
    p_platform.add_argument(
        "--cargo-toml",
        type=Path,
        default=Path("Cargo.toml"),
        help="Path to workspace Cargo.toml (default: Cargo.toml)",
    )

    # -- wrapper subcommand --
    p_wrapper = sub.add_parser("wrapper", help="Generate the wrapper npm package")
    p_wrapper.add_argument(
        "--npm-dir",
        type=Path,
        default=Path("npm"),
        help="Directory containing JS source files (default: npm/)",
    )
    p_wrapper.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist/npm"),
        help="Output directory (default: dist/npm/)",
    )
    p_wrapper.add_argument(
        "--cargo-toml",
        type=Path,
        default=Path("Cargo.toml"),
        help="Path to workspace Cargo.toml (default: Cargo.toml)",
    )

    args = parser.parse_args(argv)
    license_dir = args.cargo_toml.parent

    if args.command == "platform":
        if args.binary_dir is None:
            args.binary_dir = Path("target") / args.target / "release"

        pkg_dir = build_platform_package(
            target=args.target,
            binary_dir=args.binary_dir,
            output_dir=args.output_dir,
            cargo_toml=args.cargo_toml,
            license_dir=license_dir,
        )
        print(f"Built platform package: {pkg_dir}")

    elif args.command == "wrapper":
        pkg_dir = build_wrapper_package(
            npm_source_dir=args.npm_dir,
            output_dir=args.output_dir,
            cargo_toml=args.cargo_toml,
            license_dir=license_dir,
        )
        print(f"Built wrapper package: {pkg_dir}")


if __name__ == "__main__":
    main()
