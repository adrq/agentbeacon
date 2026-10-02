# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent


def test_mock_agent_script_resolves():
    result = subprocess.run(
        ["uv", "run", "mock-agent", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(BASE_DIR),
    )
    assert result.returncode == 0, f"mock-agent --help failed: {result.stderr}"
