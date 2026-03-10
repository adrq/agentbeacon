"""Publish AgentBeacon artifacts to PyPI, npm, and GitHub Releases.

Usage:
    python scripts/publish.py pypi [--dry-run] [--publish-url URL] [--check-url URL]
    python scripts/publish.py npm [--dry-run]
    python scripts/publish.py github [--dry-run] [--tag TAG]
    python scripts/publish.py all [--dry-run] [--tag TAG] [--publish-url URL] [--check-url URL]

Auth: tokens are never accepted as CLI flags (process-list exposure risk).
Supported credential sources — env vars, OIDC, and local credential stores:
    UV_PUBLISH_TOKEN  — PyPI token (or OIDC trusted publishing via uv)
    NPM_TOKEN         — npm auth token (or existing ~/.npmrc / OIDC)
    GITHUB_TOKEN      — GitHub token (or gh auth login)

Stdlib only -- no third-party dependencies.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

PACKAGE_NAME = "agentbeacon"
BINARIES = ["agentbeacon", "agentbeacon-worker"]
NPM_SCOPE = "@agentbeacon"

TARGETS = [
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
]

TARGET_TO_NPM_SUFFIX: dict[str, str] = {
    "x86_64-unknown-linux-musl": "linux-x64",
    "aarch64-unknown-linux-musl": "linux-arm64",
}

TARGET_TO_WHEEL_TAG: dict[str, str] = {
    "x86_64-unknown-linux-musl": "manylinux_2_17_x86_64",
    "aarch64-unknown-linux-musl": "manylinux_2_17_aarch64",
}


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


def is_prerelease(version: str) -> bool:
    """Return True if the SemVer version has a pre-release suffix."""
    return "-" in version


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
# Tool checks
# ---------------------------------------------------------------------------


def require_tool(name: str) -> Path:
    """Verify a CLI tool is on PATH. Returns its path. Raises if missing."""
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(
            f"Required tool '{name}' not found on PATH. "
            f"Install it before running this script."
        )
    return Path(path)


# ---------------------------------------------------------------------------
# Tarball generation
# ---------------------------------------------------------------------------


def tarball_name(version: str, target: str) -> str:
    """Return the tarball filename for a version + target."""
    return f"{PACKAGE_NAME}-{version}-{target}.tar.gz"


def build_tarball(
    version: str, target: str, binary_dir: Path, output_dir: Path
) -> Path:
    """Create a .tar.gz with both binaries for a target.

    Flat archive (no top-level directory). Fixed mtime for reproducibility.
    """
    for name in BINARIES:
        path = binary_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"Binary not found: {path}. "
                f"Build with: make build-musl-x64 or make build-musl-arm64"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = output_dir / tarball_name(version, target)

    # Use gzip.GzipFile with mtime=0 to avoid embedding the current
    # timestamp in the gzip header, ensuring byte-level reproducibility.
    with gzip.GzipFile(tarball_path, "wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name in BINARIES:
                src = binary_dir / name
                info = tar.gettarinfo(str(src), arcname=name)
                info.mtime = 0
                info.mode = 0o755
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                with open(src, "rb") as f:
                    tar.addfile(info, f)

    return tarball_path


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------


def sha256_hex(path: Path) -> str:
    """Return hex SHA256 digest of a file (64KB chunked reads)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def generate_checksums(files: list[Path], output_dir: Path) -> Path:
    """Write a SHA256SUMS file in sha256sum-compatible format.

    Format: ``<hash>  <filename>\\n`` (two spaces between hash and name).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    checksums_path = output_dir / "SHA256SUMS"
    lines = []
    for f in files:
        digest = sha256_hex(f)
        lines.append(f"{digest}  {f.name}\n")
    checksums_path.write_text("".join(lines))
    return checksums_path


# ---------------------------------------------------------------------------
# Artifact discovery
# ---------------------------------------------------------------------------


def find_wheels(dist_dir: Path, version: str) -> list[Path]:
    """Find .whl files for the given version. Returns sorted list. Raises if empty."""
    if not dist_dir.is_dir():
        raise FileNotFoundError(
            f"Distribution directory not found: {dist_dir}. "
            f"Build with: make build-wheels"
        )
    wheels = sorted(dist_dir.glob(f"{PACKAGE_NAME}-{version}-*.whl"))
    if not wheels:
        raise FileNotFoundError(
            f"No .whl files for version {version} in {dist_dir}. "
            f"Build with: make build-wheels"
        )
    return wheels


def validate_wheel_set(wheels: list[Path]) -> None:
    """Verify exactly one wheel per expected platform tag exists.

    Raises FileNotFoundError listing missing, duplicate, or unexpected platforms.
    """
    expected_tags = set(TARGET_TO_WHEEL_TAG.values())
    tag_to_wheels: dict[str, list[Path]] = {t: [] for t in expected_tags}
    unmatched: list[Path] = []

    for w in wheels:
        matched = [t for t in expected_tags if t in w.name]
        if matched:
            tag_to_wheels[matched[0]].append(w)
        else:
            unmatched.append(w)

    missing = sorted(t for t, ws in tag_to_wheels.items() if len(ws) == 0)
    if missing:
        raise FileNotFoundError(
            f"Missing wheels for platforms: {', '.join(missing)}. "
            f"Build with: make build-wheels"
        )

    duplicates = {t: ws for t, ws in tag_to_wheels.items() if len(ws) > 1}
    if duplicates:
        detail = "; ".join(
            f"{t}: {', '.join(w.name for w in ws)}"
            for t, ws in sorted(duplicates.items())
        )
        raise FileNotFoundError(
            f"Multiple wheels for same platform: {detail}. "
            f"Remove stale wheels from dist/"
        )

    if unmatched:
        names = ", ".join(w.name for w in unmatched)
        raise FileNotFoundError(f"Unexpected wheels found: {names}")


def validate_npm_platform_set(platform_dirs: list[Path]) -> None:
    """Verify exactly one npm platform package per expected target exists.

    Raises FileNotFoundError listing missing or unexpected platform packages.
    """
    expected_suffixes = set(TARGET_TO_NPM_SUFFIX.values())
    found_suffixes: set[str] = set()
    for d in platform_dirs:
        for suffix in expected_suffixes:
            if d.name == f"cli-{suffix}":
                found_suffixes.add(suffix)

    missing = expected_suffixes - found_suffixes
    if missing:
        names = ", ".join(f"cli-{s}" for s in sorted(missing))
        raise FileNotFoundError(
            f"Missing npm platform packages: {names}. "
            f"Build with: make build-npm-x64 build-npm-arm64"
        )

    expected_names = {f"cli-{s}" for s in expected_suffixes}
    unexpected = [d for d in platform_dirs if d.name not in expected_names]
    if unexpected:
        names = ", ".join(d.name for d in unexpected)
        raise FileNotFoundError(f"Unexpected npm platform packages: {names}")


def validate_npm_versions(
    platform_dirs: list[Path], wrapper_dir: Path, version: str
) -> None:
    """Verify all npm package.json files have the expected name and version.

    Raises ValueError listing any mismatched packages.
    """
    # Build expected name for each package directory.
    expected_names: dict[Path, str] = {wrapper_dir: PACKAGE_NAME}
    for d in platform_dirs:
        expected_names[d] = f"{NPM_SCOPE}/{d.name}"

    version_mismatched: list[str] = []
    name_mismatched: list[str] = []
    for pkg_dir in [*platform_dirs, wrapper_dir]:
        pkg_json = pkg_dir / "package.json"
        data = json.loads(pkg_json.read_text())

        pkg_name = data.get("name", "<missing>")
        expected_name = expected_names[pkg_dir]
        if pkg_name != expected_name:
            name_mismatched.append(
                f"{pkg_dir.name}: got {pkg_name}, expected {expected_name}"
            )

        pkg_version = data.get("version", "<missing>")
        if pkg_version != version:
            version_mismatched.append(f"{pkg_dir.name}: {pkg_version}")

    if name_mismatched:
        detail = ", ".join(name_mismatched)
        raise ValueError(
            f"npm package name mismatch: {detail}. "
            f"Rebuild with: make build-npm-x64 build-npm-arm64 build-npm-wrapper"
        )

    if version_mismatched:
        detail = ", ".join(version_mismatched)
        raise ValueError(
            f"npm package version mismatch (expected {version}): {detail}. "
            f"Rebuild with: make build-npm-x64 build-npm-arm64 build-npm-wrapper"
        )


def find_npm_packages(dist_npm_dir: Path) -> tuple[list[Path], Path]:
    """Discover npm platform and wrapper package directories.

    Returns (platform_dirs, wrapper_dir). Platform dirs are sorted.
    Validates package.json exists in each directory.
    """
    if not dist_npm_dir.is_dir():
        raise FileNotFoundError(
            f"npm distribution directory not found: {dist_npm_dir}. "
            f"Build with: make build-npm-x64 build-npm-arm64 build-npm-wrapper"
        )

    # Discover platform packages via glob.
    platform_dirs = sorted(dist_npm_dir.glob(f"{NPM_SCOPE}/cli-*"))
    if not platform_dirs:
        raise FileNotFoundError(
            f"No platform packages found in {dist_npm_dir}/{NPM_SCOPE}/. "
            f"Build with: make build-npm-x64 build-npm-arm64"
        )

    for pkg_dir in platform_dirs:
        pkg_json = pkg_dir / "package.json"
        if not pkg_json.is_file():
            raise FileNotFoundError(
                f"Missing package.json in platform package: {pkg_dir}"
            )

    # Wrapper package.
    wrapper_dir = dist_npm_dir / PACKAGE_NAME
    if not wrapper_dir.is_dir():
        raise FileNotFoundError(
            f"Wrapper package not found: {wrapper_dir}. "
            f"Build with: make build-npm-wrapper"
        )
    wrapper_json = wrapper_dir / "package.json"
    if not wrapper_json.is_file():
        raise FileNotFoundError(
            f"Missing package.json in wrapper package: {wrapper_dir}"
        )

    return platform_dirs, wrapper_dir


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def run_cmd(
    cmd: list[str],
    *,
    dry_run: bool = False,
    dry_run_label: str = "",
) -> subprocess.CompletedProcess[bytes]:
    """Run a subprocess with check=True, or print the command if dry_run.

    stdout/stderr are inherited (never captured) to prevent token leaks
    in error messages.
    """
    if dry_run:
        label = f" ({dry_run_label})" if dry_run_label else ""
        print(f"[dry-run] Would run{label}: {shlex.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0)

    print(f"Running: {shlex.join(cmd)}")
    return subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Publish: PyPI
# ---------------------------------------------------------------------------


def publish_pypi(
    dist_dir: Path,
    version: str,
    *,
    dry_run: bool = False,
    publish_url: str | None = None,
    check_url: str | None = None,
) -> None:
    """Publish wheels to PyPI via uv publish."""
    require_tool("uv")
    wheels = find_wheels(dist_dir, semver_to_pep440(version))
    validate_wheel_set(wheels)

    cmd = ["uv", "publish"]
    if dry_run:
        cmd.append("--dry-run")
    if publish_url:
        cmd.extend(["--publish-url", publish_url])
    if check_url:
        cmd.extend(["--check-url", check_url])
    cmd.extend(str(w) for w in wheels)

    # Always run the real command — uv's --dry-run handles simulation.
    run_cmd(cmd, dry_run=False)


# ---------------------------------------------------------------------------
# Publish: npm
# ---------------------------------------------------------------------------


def publish_npm(dist_npm_dir: Path, version: str, *, dry_run: bool = False) -> None:
    """Publish npm packages (platform packages first, then wrapper)."""
    require_tool("npm")
    platform_dirs, wrapper_dir = find_npm_packages(dist_npm_dir)
    validate_npm_platform_set(platform_dirs)
    validate_npm_versions(platform_dirs, wrapper_dir, version)

    # Temp .npmrc for auth if NPM_TOKEN is set.
    npm_token = os.environ.get("NPM_TOKEN")
    npmrc_path: str | None = None
    auth_args: list[str] = []

    if npm_token:
        fd, npmrc_path = tempfile.mkstemp(suffix=".npmrc")
        with os.fdopen(fd, "w") as f:
            f.write(f"//registry.npmjs.org/:_authToken={npm_token}\n")
        auth_args = ["--userconfig", npmrc_path]

    tag_args = ["--tag", "next"] if is_prerelease(version) else []
    npm_propagation_delay = 10

    try:
        for pkg_dir in platform_dirs:
            cmd = (
                ["npm", "publish", str(pkg_dir), "--access", "public"]
                + auth_args
                + tag_args
                + (["--dry-run"] if dry_run else [])
            )
            run_cmd(cmd, dry_run=False)

        if not dry_run:
            print(
                f"Waiting {npm_propagation_delay}s for platform package propagation..."
            )
            time.sleep(npm_propagation_delay)

        cmd = (
            ["npm", "publish", str(wrapper_dir), "--access", "public"]
            + auth_args
            + tag_args
            + (["--dry-run"] if dry_run else [])
        )
        run_cmd(cmd, dry_run=False)
    finally:
        if npmrc_path:
            Path(npmrc_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Publish: GitHub Releases
# ---------------------------------------------------------------------------


def publish_github(
    version: str,
    tag: str,
    dist_dir: Path,
    binary_base_dir: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Create a GitHub Release with tarballs and checksums."""
    require_tool("gh")

    print(f"GitHub Release: tag={tag}, version={version}")

    # Build tarballs for each target.
    tarball_dir = dist_dir / "tarballs"
    tarball_paths: list[Path] = []
    for target in TARGETS:
        binary_dir = binary_base_dir / target / "release"
        tb = build_tarball(version, target, binary_dir, tarball_dir)
        tarball_paths.append(tb)
        print(f"  Built tarball: {tb.name} ({tb.stat().st_size:,} bytes)")

    # Generate checksums.
    checksums_path = generate_checksums(tarball_paths, tarball_dir)
    print(f"  Checksums:\n{checksums_path.read_text()}", end="")

    # All assets to upload.
    assets = tarball_paths + [checksums_path]

    cmd = [
        "gh",
        "release",
        "create",
        tag,
        "--verify-tag",
        "--title",
        f"{PACKAGE_NAME} {version}",
        "--generate-notes",
    ]
    if is_prerelease(version):
        cmd.append("--prerelease")
    cmd.extend(str(a) for a in assets)

    # For dry-run: skip gh execution (no native --dry-run support).
    run_cmd(cmd, dry_run=dry_run, dry_run_label="gh release create")


# ---------------------------------------------------------------------------
# Publish: all channels
# ---------------------------------------------------------------------------


def publish_all(
    version: str,
    tag: str,
    dist_dir: Path,
    dist_npm_dir: Path,
    binary_base_dir: Path,
    *,
    dry_run: bool = False,
    publish_url: str | None = None,
    check_url: str | None = None,
) -> None:
    """Publish to all channels in sequence: PyPI → npm → GitHub.

    Fail-fast: reports completed channels on error.
    """
    completed: list[str] = []
    try:
        publish_pypi(
            dist_dir,
            version,
            dry_run=dry_run,
            publish_url=publish_url,
            check_url=check_url,
        )
        completed.append("pypi")

        publish_npm(dist_npm_dir, version, dry_run=dry_run)
        completed.append("npm")

        publish_github(version, tag, dist_dir, binary_base_dir, dry_run=dry_run)
        completed.append("github")

    except Exception:
        if completed:
            print(
                f"\nPublish failed. Completed channels: {', '.join(completed)}. "
                f"Re-run failed channel individually."
            )
        raise

    print(f"\nAll channels published: {', '.join(completed)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Publish AgentBeacon artifacts to PyPI, npm, and GitHub Releases."
    )
    parser.add_argument(
        "--cargo-toml",
        type=Path,
        default=Path("Cargo.toml"),
        help="Path to workspace Cargo.toml (default: Cargo.toml)",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("dist"),
        help="Directory containing build artifacts (default: dist/)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # -- pypi --
    p_pypi = sub.add_parser("pypi", help="Publish wheels to PyPI")
    p_pypi.add_argument("--dry-run", action="store_true", help="Simulate publish")
    p_pypi.add_argument(
        "--publish-url", default=None, help="Custom publish URL (e.g. TestPyPI)"
    )
    p_pypi.add_argument(
        "--check-url", default=None, help="Check URL for skip-duplicate support"
    )

    # -- npm --
    p_npm = sub.add_parser("npm", help="Publish npm packages")
    p_npm.add_argument("--dry-run", action="store_true", help="Simulate publish")
    p_npm.add_argument(
        "--dist-npm-dir",
        type=Path,
        default=None,
        help="npm package directory (default: <dist-dir>/npm/)",
    )

    # -- github --
    p_github = sub.add_parser("github", help="Create GitHub Release")
    p_github.add_argument("--dry-run", action="store_true", help="Simulate publish")
    p_github.add_argument(
        "--tag", default=None, help="Git tag (default: v<version> from Cargo.toml)"
    )
    p_github.add_argument(
        "--binary-base-dir",
        type=Path,
        default=Path("target"),
        help="Base directory for target binaries (default: target/)",
    )

    # -- all --
    p_all = sub.add_parser("all", help="Publish to all channels")
    p_all.add_argument("--dry-run", action="store_true", help="Simulate publish")
    p_all.add_argument(
        "--tag", default=None, help="Git tag (default: v<version> from Cargo.toml)"
    )
    p_all.add_argument(
        "--publish-url", default=None, help="Custom publish URL (e.g. TestPyPI)"
    )
    p_all.add_argument(
        "--check-url", default=None, help="Check URL for skip-duplicate support"
    )
    p_all.add_argument(
        "--dist-npm-dir",
        type=Path,
        default=None,
        help="npm package directory (default: <dist-dir>/npm/)",
    )
    p_all.add_argument(
        "--binary-base-dir",
        type=Path,
        default=Path("target"),
        help="Base directory for target binaries (default: target/)",
    )

    args = parser.parse_args(argv)
    version = extract_version(args.cargo_toml)

    if args.command == "pypi":
        publish_pypi(
            args.dist_dir,
            version,
            dry_run=args.dry_run,
            publish_url=args.publish_url,
            check_url=args.check_url,
        )

    elif args.command == "npm":
        dist_npm_dir = args.dist_npm_dir or args.dist_dir / "npm"
        publish_npm(dist_npm_dir, version, dry_run=args.dry_run)

    elif args.command == "github":
        tag = args.tag or f"v{version}"
        publish_github(
            version,
            tag,
            args.dist_dir,
            args.binary_base_dir,
            dry_run=args.dry_run,
        )

    elif args.command == "all":
        tag = args.tag or f"v{version}"
        dist_npm_dir = args.dist_npm_dir or args.dist_dir / "npm"
        publish_all(
            version,
            tag,
            args.dist_dir,
            dist_npm_dir,
            args.binary_base_dir,
            dry_run=args.dry_run,
            publish_url=args.publish_url,
            check_url=args.check_url,
        )


if __name__ == "__main__":
    main()
