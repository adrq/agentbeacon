"""File logging utilities for mock agent test verification."""

import re
import fcntl
import time
from datetime import datetime
from pathlib import Path
from typing import Dict


def parse_agent_entry(line: str) -> Dict:
    """Parse unified format: [execution_id][node_id] timestamp task_text"""
    # Match [exec_id][node_id] timestamp rest_of_text
    match = re.match(r"\[([^\]]+)\]\[([^\]]+)\]\s+(\S+)\s+(.*)", line)
    if match:
        execution_id, node_id, timestamp, task_text = match.groups()
        return {
            "execution_id": execution_id,
            "node_id": node_id,
            "timestamp": timestamp,
            "task_text": task_text,
        }

    # Fallback for prompts without special format
    return {
        "execution_id": "default",
        "node_id": "default",
        "timestamp": "NOW",
        "task_text": line,
    }


def log_task_completion(prompt: str) -> None:
    """Parse prompt and append task completion to test-specific log file.

    Args:
        prompt: Input prompt (may contain bracketed format or be plain text)

    Behavior:
        - Uses PYTEST_CURRENT_TEST env var for automatic file naming
        - Creates logs directory if needed
        - Uses fcntl file locking for concurrent safety
        - Replaces NOW timestamp with actual ISO timestamp
        - Never raises exceptions (logs errors to stderr)
    """
    try:
        # Get sanitized test name from pytest environment
        from tests.testhelpers import get_current_test_name

        test_name = get_current_test_name("unknown_test")

        # Parse prompt using unified parsing function
        parsed = parse_agent_entry(prompt)

        # Replace NOW with actual ISO timestamp
        if parsed["timestamp"] == "NOW":
            # Format as ISO without microseconds to match test expectations
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            timestamp = parsed["timestamp"]

        # Format log entry
        log_entry = f"[{parsed['execution_id']}][{parsed['node_id']}] {timestamp} {parsed['task_text']}\n"

        # Create logs directory if needed
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)

        # Append to log file with file locking
        log_file = logs_dir / f"{test_name}.log"

        # Retry mechanism for file locking
        max_retries = 3
        retry_delay = 0.1  # 100ms

        for attempt in range(max_retries):
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    # Use fcntl file locking
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        f.write(log_entry)
                        f.flush()  # Immediate write for real-time test verification
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

                # Success, break retry loop
                break

            except (OSError, IOError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    # Log error to stderr but don't raise
                    import sys

                    print(
                        f"Warning: Failed to log task completion: {e}", file=sys.stderr
                    )

    except Exception as e:
        # Catch any other errors and log to stderr
        import sys

        print(f"Warning: Error in log_task_completion: {e}", file=sys.stderr)
