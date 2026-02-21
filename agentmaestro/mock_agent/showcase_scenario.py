"""Showcase scenario — non-interactive demo of all renderer types.

Single-phase: sends thinking, tool calls, plan, markdown, and a
final summary message. No questions, no user interaction.
"""

import asyncio
import json
import os


class ShowcaseScenario:
    def __init__(self, session_id: str, mcp_client=None):
        delay_ms = int(os.environ.get("MOCK_AGENT_DELAY_MS", "1200"))
        self.session_id = session_id
        self.delay = delay_ms / 1000.0

    async def handle_prompt(self, prompt_text: str) -> str:
        d = self.delay

        # 1. Thinking
        self._send_thought(
            "Let me analyze the codebase and figure out the best approach..."
        )
        await asyncio.sleep(d)

        # 2. Tool call — read file
        self._send_tool_call("read_file", "src/config.rs")
        await asyncio.sleep(d)

        # 3. Message chunk
        self._send_message("Found the configuration module. Checking dependencies...")
        await asyncio.sleep(d)

        # 4. Tool call — search
        self._send_tool_call("grep", "TODO|FIXME across 12 files")
        await asyncio.sleep(d)

        # 5. Plan
        self._send_plan(
            [
                {
                    "id": "step-1",
                    "title": "Audit existing config parsing",
                    "status": "completed",
                },
                {
                    "id": "step-2",
                    "title": "Refactor validation logic",
                    "status": "completed",
                },
                {"id": "step-3", "title": "Add unit tests", "status": "in_progress"},
                {"id": "step-4", "title": "Update documentation", "status": "pending"},
            ]
        )
        await asyncio.sleep(d)

        # 6. Another tool call — write file
        self._send_tool_call("write_file", "src/config.rs (47 lines changed)")
        await asyncio.sleep(d)

        # 7. Tool call update — completed
        self._send_tool_call_update(
            "showcase-write_file", "Write src/config.rs", "completed"
        )
        await asyncio.sleep(d)

        # 8. Markdown response with code, table, and formatting
        self._send_message(
            "# Refactoring Complete\n\n"
            "## Changes Made\n\n"
            "Refactored the configuration module to use **strongly-typed validation** "
            "with proper error handling.\n\n"
            "| File | Changes | Status |\n"
            "|------|---------|--------|\n"
            "| `src/config.rs` | +47 -23 | Modified |\n"
            "| `src/config_test.rs` | +89 -0 | Created |\n"
            "| `src/main.rs` | +3 -5 | Modified |\n\n"
            "### Key Changes\n\n"
            "```rust\n"
            "pub fn validate_config(config: &Config) -> Result<(), ConfigError> {\n"
            "    if config.port == 0 {\n"
            '        return Err(ConfigError::InvalidPort("port must be > 0"));\n'
            "    }\n"
            "    if config.workers > 64 {\n"
            '        return Err(ConfigError::InvalidWorkers("max 64 workers"));\n'
            "    }\n"
            "    Ok(())\n"
            "}\n"
            "```\n\n"
            "- Added `ConfigError` enum with descriptive variants\n"
            "- All 12 `TODO` items resolved\n"
            "- Test coverage increased from 34% to 91%\n\n"
            "> **Note**: Run `cargo test` to verify all 89 new tests pass."
        )

        return "end_turn"

    def _send_message(self, text: str):
        self._emit(
            "session/update",
            {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        )

    def _send_thought(self, text: str):
        self._emit(
            "session/update",
            {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        )

    def _send_tool_call(self, tool_name: str, title: str):
        self._emit(
            "session/update",
            {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": f"showcase-{tool_name}",
                    "title": f"{tool_name}({title})",
                },
            },
        )

    def _send_plan(self, entries: list):
        self._emit(
            "session/update",
            {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "plan",
                    "entries": entries,
                },
            },
        )

    def _send_tool_call_update(self, tool_call_id: str, title: str, status: str):
        self._emit(
            "session/update",
            {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool_call_id,
                    "title": title,
                    "status": status,
                },
            },
        )

    def _emit(self, method: str, params: dict):
        print(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params}),
            flush=True,
        )
