"""Scripted demo scenario for E2E testing.

Plays a multi-phase sequence across session/prompt calls:
  Phase 0 (initial prompt): sends message chunks + tool call + escalate via MCP
  Phase 1 (after first answer): acknowledges answer, asks a second question via MCP
  Phase 2 (after second answer): acknowledges answer and completes
"""

import asyncio
import json
import os
import signal
import sys
from typing import Optional

from .mcp_client import McpClient


class DemoScenario:
    def __init__(self, session_id: str, mcp_client: Optional[McpClient]):
        delay_ms = int(os.environ.get("MOCK_AGENT_DELAY_MS", "1000"))
        self.session_id = session_id
        self.mcp_client = mcp_client
        self.delay = delay_ms / 1000.0
        self.phase = 0

    async def handle_prompt(self, prompt_text: str) -> str:
        """Run the current phase and return stopReason."""
        # Let process-killing commands through even in demo mode
        if prompt_text.strip().upper() in ("EXIT_1", "FAIL_NODE"):
            os.kill(os.getpid(), signal.SIGKILL)

        if self.phase == 0:
            return await self._phase_initial(prompt_text)
        elif self.phase == 1:
            return await self._phase_after_first_answer(prompt_text)
        elif self.phase == 2:
            return await self._phase_after_second_answer(prompt_text)
        else:
            return "end_turn"

    async def _phase_initial(self, prompt_text: str) -> str:
        self._send_message("Analyzing the project...")
        await asyncio.sleep(self.delay)

        self._send_tool_call("read_file", "src/main.rs")
        await asyncio.sleep(self.delay * 0.5)

        self._send_message("Found the issue. Let me check something with you.")
        await asyncio.sleep(self.delay * 0.5)

        if self.mcp_client:
            try:
                await self.mcp_client.call_tool(
                    "escalate",
                    {
                        "questions": [
                            {
                                "question": "Which approach should I take?",
                                "options": [
                                    {
                                        "label": "Refactor existing code",
                                        "description": "Modify the existing implementation",
                                    },
                                    {
                                        "label": "Write new module",
                                        "description": "Create a fresh implementation",
                                    },
                                    {
                                        "label": "Add a wrapper",
                                        "description": "Wrap the existing code with a new interface",
                                    },
                                ],
                            }
                        ],
                        "importance": "blocking",
                    },
                )
            except Exception as e:
                print(f"MCP escalate failed: {e}", file=sys.stderr)
                self.phase = 1
                return "error"

        self.phase = 1
        return "end_turn"

    async def _phase_after_first_answer(self, prompt_text: str) -> str:
        answer = prompt_text.strip()
        self._send_message(f"Got it, going with: {answer}")
        await asyncio.sleep(self.delay)
        self._send_message("One more thing — I need a follow-up decision.")
        await asyncio.sleep(self.delay * 0.5)

        if self.mcp_client:
            try:
                await self.mcp_client.call_tool(
                    "escalate",
                    {
                        "questions": [
                            {
                                "question": "How should I handle edge cases?",
                                "options": [
                                    {
                                        "label": "Strict validation",
                                        "description": "Reject invalid inputs",
                                    },
                                    {
                                        "label": "Lenient parsing",
                                        "description": "Best-effort with fallbacks",
                                    },
                                ],
                            }
                        ],
                        "importance": "blocking",
                    },
                )
            except Exception as e:
                print(f"MCP escalate (phase 1) failed: {e}", file=sys.stderr)
                self.phase = 2
                return "error"

        self.phase = 2
        return "end_turn"

    async def _phase_after_second_answer(self, prompt_text: str) -> str:
        answer = prompt_text.strip()
        self._send_message(f"Perfect, using: {answer}")
        await asyncio.sleep(self.delay)
        self._send_message("Done! Applied the changes successfully.")
        self.phase = 3
        return "end_turn"

    def _send_message(self, text: str):
        notification = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        }
        print(json.dumps(notification), flush=True)

    def _send_tool_call(self, tool_name: str, title: str):
        notification = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": self.session_id,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": f"demo-{tool_name}",
                    "title": f"{tool_name}({title})",
                },
            },
        }
        print(json.dumps(notification), flush=True)
