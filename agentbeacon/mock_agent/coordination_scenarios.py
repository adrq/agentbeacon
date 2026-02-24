"""Coordination scenarios for E2E testing of lead-child delegation.

Each scenario is a phase-based state machine driven by successive session/prompt
calls. Phases advance on each prompt; marker text is emitted as agent_message_chunk
notifications so tests can assert on deterministic strings.

Scenarios:
  DelegateScenario — lead delegates to a child, acknowledges handoff result
  HandoffScenario — child hands off result back to lead
  DelegateAskScenario — lead delegates, then asks user after handoff result
  DelegateMultiScenario — lead delegates N children sequentially
"""

import json

from .mcp_client import McpClient


class _BaseScenario:
    """Shared utilities for coordination scenarios."""

    def __init__(self, session_id: str, mcp_client: McpClient):
        if mcp_client is None:
            raise RuntimeError("Coordination scenario requires mcp_client (MCP URL)")
        self.session_id = session_id
        self.mcp_client = mcp_client
        self.phase = 0

    def _send_marker(self, text: str):
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


class DelegateScenario(_BaseScenario):
    """Lead agent: delegates to a child, then acknowledges the handoff result.

    Requires delegate_to (child agent name).

    Phase 0 (initial prompt): emits DELEGATE_PHASE_0, calls delegate → end_turn
    Phase 1 (handoff result):  emits DELEGATE_PHASE_1_ACK → end_turn
    """

    def __init__(
        self,
        session_id: str,
        mcp_client: McpClient,
        delegate_to: str,
    ):
        super().__init__(session_id, mcp_client)
        if not delegate_to:
            raise ValueError("DelegateScenario requires delegate_to")
        self.delegate_to = delegate_to

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase == 0:
            self._send_marker("DELEGATE_PHASE_0")
            await self.mcp_client.call_tool(
                "delegate",
                {"agent": self.delegate_to, "prompt": f"Child task: {prompt_text}"},
            )
            self.phase = 1
            return "end_turn"

        # Phase 1+: handoff result delivered
        self._send_marker("DELEGATE_PHASE_1_ACK")
        self.phase += 1
        return "end_turn"


class HandoffScenario(_BaseScenario):
    """Child agent: hands off result back to lead.

    Phase 0 (initial prompt): calls handoff with message → end_turn
    """

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase > 0:
            return "end_turn"
        excerpt = prompt_text[:80] if prompt_text else "done"
        await self.mcp_client.call_tool(
            "handoff",
            {"message": f"Completed: {excerpt}"},
        )
        self.phase = 1
        return "end_turn"


class DelegateAskScenario(_BaseScenario):
    """Lead agent: delegates, receives handoff, then asks the user a question.

    Phase 0 (initial prompt): emits DELEGATE_ASK_PHASE_0, calls delegate → end_turn
    Phase 1 (handoff result):  emits DELEGATE_ASK_PHASE_1, calls ask_user → end_turn
    Phase 2 (user answer):     emits DELEGATE_ASK_PHASE_2_ACK → end_turn
    """

    def __init__(
        self,
        session_id: str,
        mcp_client: McpClient,
        delegate_to: str,
    ):
        super().__init__(session_id, mcp_client)
        if not delegate_to:
            raise ValueError("DelegateAskScenario requires delegate_to")
        self.delegate_to = delegate_to

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase == 0:
            self._send_marker("DELEGATE_ASK_PHASE_0")
            await self.mcp_client.call_tool(
                "delegate",
                {"agent": self.delegate_to, "prompt": f"Child task: {prompt_text}"},
            )
            self.phase = 1
            return "end_turn"

        if self.phase == 1:
            self._send_marker("DELEGATE_ASK_PHASE_1")
            await self.mcp_client.call_tool(
                "ask_user",
                {
                    "questions": [
                        {
                            "question": "Approve the result?",
                            "options": [
                                {"label": "Yes", "description": "Approve"},
                                {"label": "No", "description": "Reject"},
                            ],
                        }
                    ],
                    "importance": "blocking",
                },
            )
            self.phase = 2
            return "end_turn"

        # Phase 2+: user answer delivered
        self._send_marker("DELEGATE_ASK_PHASE_2_ACK")
        self.phase += 1
        return "end_turn"


class DelegateMultiScenario(_BaseScenario):
    """Lead agent: delegates to N children sequentially, acknowledges each.

    Phase 0: emits DELEGATE_MULTI_PHASE_0, calls delegate N times → end_turn
    Phases 1..N: receives handoff results one at a time,
                 emits DELEGATE_MULTI_PHASE_{n}_ACK → end_turn
    """

    def __init__(
        self,
        session_id: str,
        mcp_client: McpClient,
        delegate_to: str,
        delegate_count: int = 2,
    ):
        super().__init__(session_id, mcp_client)
        if not delegate_to:
            raise ValueError("DelegateMultiScenario requires delegate_to")
        self.delegate_to = delegate_to
        self.delegate_count = delegate_count

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase == 0:
            self._send_marker("DELEGATE_MULTI_PHASE_0")
            for i in range(self.delegate_count):
                await self.mcp_client.call_tool(
                    "delegate",
                    {
                        "agent": self.delegate_to,
                        "prompt": f"Child task {i + 1}: {prompt_text}",
                    },
                )
            self.phase = 1
            return "end_turn"

        # Phases 1..N: each handoff result
        self._send_marker(f"DELEGATE_MULTI_PHASE_{self.phase}_ACK")
        self.phase += 1
        return "end_turn"
