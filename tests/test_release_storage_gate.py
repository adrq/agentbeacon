# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AGENTBEACON_TEST_RELEASE_BIN selection in tests/test_release_storage.py."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


def _run_release_storage(value: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENTBEACON_TEST_RELEASE_BIN"] = value
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "-q",
            "-m",
            "packaging",
            "-k",
            "test_release_database_dir_and_file_are_private",
            "tests/test_release_storage.py",
        ],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "value, shown",
    [("", "."), ("/nonexistent/agentbeacon", "/nonexistent/agentbeacon")],
    ids=["empty", "missing-file"],
)
def test_explicit_release_bin_that_is_not_a_file_fails(value, shown):
    result = _run_release_storage(value)
    assert result.returncode == 1, result.stdout
    assert (
        f"Failed: AGENTBEACON_TEST_RELEASE_BIN is set but {shown} is not a file"
        in result.stdout
    )
    assert "1 error" in result.stdout.splitlines()[-1]
