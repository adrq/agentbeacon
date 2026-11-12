#!/bin/sh
# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

# Usage: scripts/check_darwin_arm64.sh BINARY...
# Exits 1 unless every BINARY is a thin arm64 Mach-O executable.

status=0
for f in "$@"; do
    if [ -f "$f" ]; then
        desc=$(file -b "$f")
    else
        desc="no such file"
    fi
    if ! printf '%s\n' "$desc" | head -n 1 | grep -Eq '^Mach-O 64-bit (arm64 executable|executable arm64)([ ,]|$)'; then
        printf 'ERROR: %s: not an arm64 Mach-O executable (file: %s).\n' "$f" "$desc" >&2
        echo "Stage the signed Mac binaries from build-macos-native in target/aarch64-apple-darwin/release/ first." >&2
        status=1
    fi
done
exit $status
