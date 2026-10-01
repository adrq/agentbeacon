# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""File logging utilities for mock agent test verification."""

import re
import fcntl
import time
from datetime import datetime
from pathlib import Path
from typing import Dict


def parse_agent_entry(line: str) -> Dict:
    match = re.match(r"\[([^\]]+)\]\[([^\]]+)\]\s+(\S+)\s+(.*)", line)
    if match:
        execution_id, node_id, timestamp, task_text = match.groups()
        return {
            "execution_id": execution_id,
            "node_id": node_id,
            "timestamp": timestamp,
            "task_text": task_text,
        }

    return {
        "execution_id": "default",
        "node_id": "default",
        "timestamp": "NOW",
        "task_text": line,
    }


def log_task_completion(prompt: str) -> None:
    try:
        from tests.testhelpers import get_current_test_name

        test_name = get_current_test_name("unknown_test")

        parsed = parse_agent_entry(prompt)

        if parsed["timestamp"] == "NOW":
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            timestamp = parsed["timestamp"]

        log_entry = f"[{parsed['execution_id']}][{parsed['node_id']}] {timestamp} {parsed['task_text']}\n"

        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)

        log_file = logs_dir / f"{test_name}.log"

        max_retries = 3
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        f.write(log_entry)
                        f.flush()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

                break

            except (OSError, IOError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    import sys

                    print(
                        f"Warning: Failed to log task completion: {e}", file=sys.stderr
                    )

    except Exception as e:
        import sys

        print(f"Warning: Error in log_task_completion: {e}", file=sys.stderr)
