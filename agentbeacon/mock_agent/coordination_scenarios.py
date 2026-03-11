"""Coordination scenarios for E2E testing of lead-child delegation.

Each scenario is a phase-based state machine driven by successive session/prompt
calls. Phases advance on each prompt; marker text is emitted as agent_message_chunk
notifications so tests can assert on deterministic strings.

Scenarios:
  DelegateScenario — lead delegates to a child, acknowledges turn-complete result
  EndTurnScenario — child does end_turn (goes to input-required)
  DelegateAskScenario — lead delegates, then asks user after turn-complete result
  DelegateMultiScenario — lead delegates N children sequentially
  DelegateReleaseScenario — lead delegates, then releases the child
  EndTurnMessageScenario — child discovers parent via REST API, sends message, ends turn
"""

import json
import os
import sys

import httpx

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
    """Lead agent: delegates to a child, then acknowledges turn-complete.

    Requires delegate_to (child agent name).

    Phase 0 (initial prompt): emits DELEGATE_PHASE_0, calls delegate → end_turn
    Phase 1 (turn-complete):   emits DELEGATE_PHASE_1_ACK → end_turn
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

        # Phase 1+: turn-complete notification delivered
        self._send_marker("DELEGATE_PHASE_1_ACK")
        self.phase += 1
        return "end_turn"


class DelegateAskScenario(_BaseScenario):
    """Lead agent: delegates, receives turn-complete, then asks the user a question.

    Phase 0 (initial prompt): emits DELEGATE_ASK_PHASE_0, calls delegate → end_turn
    Phase 1 (turn-complete):   emits DELEGATE_ASK_PHASE_1, calls escalate → end_turn
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
                "escalate",
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


class EndTurnScenario(_BaseScenario):
    """Child agent: just does end_turn on each prompt (goes to input-required).

    Phase N (each prompt): emits END_TURN_PHASE_{N} -> end_turn
    """

    async def handle_prompt(self, prompt_text: str) -> str:
        self._send_marker(f"END_TURN_PHASE_{self.phase}")
        self.phase += 1
        return "end_turn"


class EndTurnMarkdownScenario(_BaseScenario):
    """Child agent: responds with rich markdown content, then ends turn.

    Phase 0: emits rich markdown as the response message -> end_turn
    Phase N: emits END_TURN_MD_PHASE_{N} -> end_turn
    """

    MARKDOWN_RESPONSE = """\
## Analysis Complete

The refactoring identified **3 critical issues**:

- Missing null check in `parseConfig()` — can crash on empty input
- Unused import `collections.OrderedDict` in `utils.py`
- Race condition in `WorkerPool.shutdown()` when tasks > threads

### Recommended Fix

```rust
fn safe_parse(input: &str) -> Result<Config, ParseError> {
    if input.is_empty() {
        return Err(ParseError::EmptyInput);
    }
    serde_json::from_str(input).map_err(ParseError::Json)
}
```

> All three issues have automated regression tests in the PR.\
"""

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase == 0:
            self._send_marker(self.MARKDOWN_RESPONSE)
            self.phase = 1
            return "end_turn"

        self._send_marker(f"END_TURN_MD_PHASE_{self.phase}")
        self.phase += 1
        return "end_turn"


class DelegateReleaseScenario(_BaseScenario):
    """Lead agent: delegates to a child, then releases the child on next prompt.

    Requires delegate_to (child agent name).

    Phase 0 (initial prompt): emits RELEASE_PHASE_0, calls delegate -> end_turn
    Phase 1 (auto-notification from child turn-complete): acknowledges -> end_turn
    Phase 2 (triggered by user message): calls release on child_session_id -> end_turn
    """

    def __init__(self, session_id: str, mcp_client: McpClient, delegate_to: str):
        super().__init__(session_id, mcp_client)
        if not delegate_to:
            raise ValueError("DelegateReleaseScenario requires delegate_to")
        self.delegate_to = delegate_to
        self.child_session_id = None

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase == 0:
            self._send_marker("RELEASE_PHASE_0")
            result = await self.mcp_client.call_tool(
                "delegate",
                {"agent": self.delegate_to, "prompt": f"Child task: {prompt_text}"},
            )
            # Parse child session_id from delegate result
            content = result.get("content", [])
            if content:
                data = json.loads(content[0].get("text", "{}"))
                self.child_session_id = data.get("session_id")
            self.phase = 1
            return "end_turn"

        if self.phase == 1:
            # Turn-complete auto-notification from child — just acknowledge
            self._send_marker("RELEASE_PHASE_1_NOTIFY_ACK")
            self.phase = 2
            return "end_turn"

        if self.phase == 2 and self.child_session_id:
            self._send_marker("RELEASE_PHASE_2")
            await self.mcp_client.call_tool(
                "release",
                {"session_id": self.child_session_id},
            )
            self._send_marker("RELEASE_PHASE_2_ACK")
            self.phase = 3
            return "end_turn"

        self._send_marker(f"RELEASE_PHASE_{self.phase}")
        self.phase += 1
        return "end_turn"


class DelegateMultiScenario(_BaseScenario):
    """Lead agent: delegates to N children sequentially, acknowledges each.

    Phase 0: emits DELEGATE_MULTI_PHASE_0, calls delegate N times → end_turn
    Phases 1..N: receives turn-complete notifications one at a time,
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

        # Phases 1..N: each turn-complete notification
        self._send_marker(f"DELEGATE_MULTI_PHASE_{self.phase}_ACK")
        self.phase += 1
        return "end_turn"


class EndTurnMessageScenario(_BaseScenario):
    """Child agent: discovers parent via REST API, sends message, then ends turn.

    Uses AGENTBEACON_* env vars (set by ACP executor) to:
    1. Call GET /api/executions/{exec_id}/agents to discover parent hierarchical name
    2. Call POST /api/messages to send message to parent

    Phase 0: discover parent, send message, end_turn
    Phase N: end_turn (same as EndTurnScenario)
    """

    async def handle_prompt(self, prompt_text: str) -> str:
        if self.phase == 0:
            self._send_marker("END_TURN_MSG_PHASE_0")

            api_base = os.environ.get("AGENTBEACON_API_BASE", "")
            session_id = os.environ.get("AGENTBEACON_SESSION_ID", "")
            execution_id = os.environ.get("AGENTBEACON_EXECUTION_ID", "")

            if api_base and session_id and execution_id:
                try:
                    async with httpx.AsyncClient() as client:
                        # Discover all sessions in this execution
                        resp = await client.get(
                            f"{api_base}/api/executions/{execution_id}/agents",
                            headers={"Authorization": f"Bearer {session_id}"},
                        )
                        resp.raise_for_status()
                        agents = resp.json()

                        # Find my entry and get parent's hierarchical name
                        my_entry = next(
                            (a for a in agents if a["session_id"] == session_id),
                            None,
                        )
                        if my_entry and my_entry.get("parent_name"):
                            post_resp = await client.post(
                                f"{api_base}/api/messages",
                                json={
                                    "to": my_entry["parent_name"],
                                    "body": f"Status update: completed task '{prompt_text}'",
                                },
                                headers={
                                    "Authorization": f"Bearer {session_id}",
                                    "Content-Type": "application/json",
                                },
                            )
                            post_resp.raise_for_status()
                            self._send_marker("END_TURN_MSG_SENT")
                except Exception as exc:
                    print(
                        f"EndTurnMessageScenario HTTP error: {exc}",
                        file=sys.stderr,
                    )

            self.phase = 1
            return "end_turn"

        self._send_marker(f"END_TURN_MSG_PHASE_{self.phase}")
        self.phase += 1
        return "end_turn"
