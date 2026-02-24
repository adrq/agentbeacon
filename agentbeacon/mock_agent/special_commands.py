"""Special command handlers for testing mock agent behavior."""

import asyncio
import random
import sys
import time
from typing import Optional


class SpecialCommands:
    """Handler for special test commands in mock agent."""

    def __init__(self):
        self._fail_once_state = False

    def is_special_command(self, text: str) -> bool:
        """Check if text contains a special command."""
        text = text.strip().upper()
        return (
            text == "HANG"
            or text.startswith("DELAY_")
            or text == "FAIL_NODE"
            or text == "FAIL_ONCE"
            or text == "EXIT_1"
            or text == "INVALID_JSONRPC"
            or text == "STREAM_CHUNKS"
            or text == "REQUEST_PERMISSION"
            or text == "SEND_PLAN"
            or text == "SEND_TOOL_CALL"
            or text == "SEND_MODE_UPDATE"
            or text == "SEND_COMMANDS_UPDATE"
            or text == "SEND_MARKDOWN"
            or text == "SEND_THOUGHT"
            or text == "SEND_TOOL_CALL_UPDATE"
        )

    def handle_command(self, text: str, stdio_mode: bool = False) -> Optional[str]:
        """Execute special command and return response or None if should exit."""
        text = text.strip().upper()

        if text == "HANG":
            time.sleep(3600)
            return "Hang command completed after 1 hour"

        elif text.startswith("DELAY_"):
            try:
                delay_part = text[6:]
                delay_value = int(delay_part)

                # Heuristic: values >= 100 are milliseconds, < 100 are seconds.
                # This allows "DELAY_1" (1 second) and "DELAY_150" (150ms) to work intuitively
                # without requiring a suffix like "DELAY_1s" or "DELAY_150ms".
                # The threshold of 100 was chosen because delays >= 100 seconds are rare in tests.
                if delay_value >= 100:
                    delay_seconds = delay_value / 1000.0
                    time.sleep(delay_seconds)
                    return f"Delayed for {delay_value} milliseconds"
                else:
                    time.sleep(delay_value)
                    return f"Delayed for {delay_value} seconds"
            except ValueError:
                return f"Invalid delay command: {text}"

        elif text == "FAIL_NODE":
            if stdio_mode:
                return "STDIO_FAILURE"
            else:
                sys.exit(1)

        elif text == "FAIL_ONCE":
            if not self._fail_once_state:
                self._fail_once_state = True
                if random.random() < 0.5:
                    if stdio_mode:
                        return "STDIO_FAILURE"
                    else:
                        sys.exit(1)
            return "FAIL_ONCE command executed successfully"

        elif text == "EXIT_1":
            sys.exit(1)

        elif text == "INVALID_JSONRPC":
            return "INVALID_JSONRPC"

        elif text == "STREAM_CHUNKS":
            return "STREAM_CHUNKS"

        elif text == "REQUEST_PERMISSION":
            return "REQUEST_PERMISSION"

        elif text == "SEND_PLAN":
            return "SEND_PLAN"

        elif text == "SEND_TOOL_CALL":
            return "SEND_TOOL_CALL"

        elif text == "SEND_MODE_UPDATE":
            return "SEND_MODE_UPDATE"

        elif text == "SEND_COMMANDS_UPDATE":
            return "SEND_COMMANDS_UPDATE"

        elif text == "SEND_MARKDOWN":
            return "SEND_MARKDOWN"

        elif text == "SEND_THOUGHT":
            return "SEND_THOUGHT"

        elif text == "SEND_TOOL_CALL_UPDATE":
            return "SEND_TOOL_CALL_UPDATE"

        return None

    async def handle_command_async(self, text: str) -> Optional[str]:
        """Async version of handle_command for use in async contexts."""
        text = text.strip().upper()

        if text == "HANG":
            await asyncio.sleep(3600)
            return "Hang command completed after 1 hour"

        elif text.startswith("DELAY_"):
            try:
                delay_part = text[6:]
                delay_value = int(delay_part)

                if delay_value >= 100:
                    delay_seconds = delay_value / 1000.0
                    await asyncio.sleep(delay_seconds)
                    return f"Delayed for {delay_value} milliseconds"
                else:
                    await asyncio.sleep(delay_value)
                    return f"Delayed for {delay_value} seconds"
            except ValueError:
                return f"Invalid delay command: {text}"

        elif text == "FAIL_NODE":
            sys.exit(1)

        elif text == "FAIL_ONCE":
            if not self._fail_once_state:
                self._fail_once_state = True
                if random.random() < 0.5:
                    sys.exit(1)
            return "FAIL_ONCE command executed successfully"

        elif text == "EXIT_1":
            sys.exit(1)

        elif text == "INVALID_JSONRPC":
            return "INVALID_JSONRPC"

        elif text == "STREAM_CHUNKS":
            return "STREAM_CHUNKS"

        elif text == "REQUEST_PERMISSION":
            return "REQUEST_PERMISSION"

        elif text == "SEND_PLAN":
            return "SEND_PLAN"

        elif text == "SEND_TOOL_CALL":
            return "SEND_TOOL_CALL"

        elif text == "SEND_MODE_UPDATE":
            return "SEND_MODE_UPDATE"

        elif text == "SEND_COMMANDS_UPDATE":
            return "SEND_COMMANDS_UPDATE"

        elif text == "SEND_MARKDOWN":
            return "SEND_MARKDOWN"

        elif text == "SEND_THOUGHT":
            return "SEND_THOUGHT"

        elif text == "SEND_TOOL_CALL_UPDATE":
            return "SEND_TOOL_CALL_UPDATE"

        return None
