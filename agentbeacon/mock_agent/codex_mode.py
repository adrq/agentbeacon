# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Codex mode handler for JSON-RPC over stdio communication.

Implements the Codex app-server protocol (thread/turn lifecycle) as a mock
for integration testing. This is a separate protocol handler from ACP -- it
owns its own request router and session state.

Key protocol differences from ACP:
- Codex JSON-RPC omits the ``"jsonrpc": "2.0"`` field
- Method names: initialize, thread/start, thread/resume, turn/start,
  turn/steer, turn/interrupt (not session/*)
- Two-step init: initialize request + initialized notification
- Thread/turn lifecycle with item emission model
"""

import asyncio
import json
import os
import signal
import sys
import traceback
import uuid
from typing import Any, Dict, List, Optional

from .special_commands import SpecialCommands
from .file_logger import log_task_completion

STEER_REJECT_NOT_STEERABLE = "STEER_REJECT_NOT_STEERABLE"
STEER_REJECT_INPUT_ERROR = "STEER_REJECT_INPUT_ERROR"
REQUEST_APPROVAL = "REQUEST_APPROVAL"
FAIL_TURN_START = "FAIL_TURN_START"
FAIL_TURN = "FAIL_TURN"

CODEX_SPECIAL_COMMANDS = {
    STEER_REJECT_NOT_STEERABLE,
    STEER_REJECT_INPUT_ERROR,
    REQUEST_APPROVAL,
    FAIL_TURN_START,
    FAIL_TURN,
}


class CodexHandler:
    def __init__(self, custom_responses: Dict[str, str] = None):
        self.custom_responses = custom_responses or {}
        self.special_commands = SpecialCommands()

        self.initialized = False
        self.thread_id: Optional[str] = None
        self.active_turn_id: Optional[str] = None
        self.turn_counter = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._turn_interrupted = False

        self._active_turn_task: Optional[asyncio.Task] = None

        self._next_steer_behavior: Optional[str] = None

        self.pending_requests: Dict[str, asyncio.Future] = {}

    async def run(self):
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                    try:
                        await self._dispatch(msg)
                    except Exception:
                        traceback.print_exc(file=sys.stderr)
                except json.JSONDecodeError:
                    self._send(
                        {
                            "id": None,
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    )
        except (EOFError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, msg: Dict[str, Any]):
        if ("result" in msg or "error" in msg) and "method" not in msg and "id" in msg:
            await self._handle_response(msg)
            return

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            await self._handle_initialize(msg_id, msg.get("params", {}))
        elif method == "initialized":
            self.initialized = True
            print("Codex mock: initialized notification received", file=sys.stderr)
        elif method == "thread/start":
            await self._handle_thread_start(msg_id, msg.get("params", {}))
        elif method == "thread/resume":
            await self._handle_thread_resume(msg_id, msg.get("params", {}))
        elif method == "turn/start":
            await self._handle_turn_start(msg_id, msg.get("params", {}))
        elif method == "turn/steer":
            await self._handle_turn_steer(msg_id, msg.get("params", {}))
        elif method == "turn/interrupt":
            await self._handle_turn_interrupt(msg_id, msg.get("params", {}))
        elif method == "model/list":
            await self._handle_model_list(msg_id)
        else:
            if msg_id is not None:
                self._send(
                    {
                        "id": msg_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    }
                )

    async def _handle_response(self, response: Dict[str, Any]):
        request_id = response.get("id")
        if request_id and request_id in self.pending_requests:
            future = self.pending_requests.pop(request_id)
            if not future.done():
                future.set_result(response)

    # ------------------------------------------------------------------
    # Initialize handshake
    # ------------------------------------------------------------------

    async def _handle_initialize(self, msg_id: Any, params: Dict[str, Any]):
        self._send(
            {
                "id": msg_id,
                "result": {
                    "codexHome": "/tmp/mock-codex-home",
                    "userAgent": "mock-codex",
                    "platformFamily": "linux",
                    "platformOs": "linux",
                },
            }
        )

    async def _handle_model_list(self, msg_id: Any):
        if not self.initialized:
            return

        self._send(
            {
                "method": "configWarning",
                "params": {"message": "mock config warning"},
            }
        )
        self._send({"id": msg_id, "note": "not a response"})
        self._send(
            {
                "id": str(msg_id),
                "result": {
                    "data": [
                        {
                            "id": "gpt-5.6-codex",
                            "model": "gpt-5.6-codex",
                            "displayName": "GPT-5.6 Codex",
                            "description": "Mock GPT-5.6 Codex model",
                        },
                        {
                            "id": "gpt-5.6",
                            "model": "gpt-5.6",
                            "displayName": "GPT-5.6",
                            "description": "Mock GPT-5.6 model",
                        },
                    ]
                },
            }
        )

    async def _handle_thread_start(self, msg_id: Any, params: Dict[str, Any]):
        self.thread_id = f"thr_mock_{uuid.uuid4().hex[:8]}"
        thread_obj = {"id": self.thread_id}

        self._send({"method": "thread/started", "params": {"thread": thread_obj}})
        self._send({"id": msg_id, "result": {"thread": thread_obj}})

    async def _handle_thread_resume(self, msg_id: Any, params: Dict[str, Any]):
        thread_id = params.get("threadId")
        if not thread_id:
            self._send(
                {
                    "id": msg_id,
                    "error": {"code": -32602, "message": "Missing threadId"},
                }
            )
            return

        self.thread_id = thread_id
        thread_obj = {"id": self.thread_id}

        self._send({"method": "thread/started", "params": {"thread": thread_obj}})
        self._send({"id": msg_id, "result": {"thread": thread_obj}})

    async def _handle_turn_start(self, msg_id: Any, params: Dict[str, Any]):
        self.turn_counter += 1
        turn_id = f"turn_mock_{self.turn_counter:03d}"
        self.active_turn_id = turn_id
        self._turn_interrupted = False

        input_parts = params.get("input", [])
        input_text = self._extract_text(input_parts)

        cmd_upper = input_text.strip().upper()
        if cmd_upper in ("FAIL_NODE", "EXIT_1"):
            os.kill(os.getpid(), signal.SIGKILL)

        if cmd_upper == FAIL_TURN_START:
            self.active_turn_id = None
            self._send(
                {
                    "id": msg_id,
                    "error": {
                        "code": -32603,
                        "message": "Internal error: turn/start rejected for testing",
                    },
                }
            )
            return

        if cmd_upper == STEER_REJECT_NOT_STEERABLE:
            self._next_steer_behavior = STEER_REJECT_NOT_STEERABLE
        elif cmd_upper == STEER_REJECT_INPUT_ERROR:
            self._next_steer_behavior = STEER_REJECT_INPUT_ERROR
        elif cmd_upper == REQUEST_APPROVAL:
            self._next_steer_behavior = REQUEST_APPROVAL

        turn_obj = {"id": turn_id, "status": "inProgress"}

        self._send({"id": msg_id, "result": {"turn": turn_obj}})

        self._send(
            {
                "method": "turn/started",
                "params": {"threadId": self.thread_id, "turn": turn_obj},
            }
        )

        self._active_turn_task = asyncio.create_task(
            self._run_turn(turn_id, input_text, cmd_upper)
        )

    async def _run_turn(self, turn_id: str, input_text: str, cmd_upper: str):
        try:
            if cmd_upper == FAIL_TURN:
                self.active_turn_id = None
                self._active_turn_task = None
                self._send(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": self.thread_id,
                            "turn": {
                                "id": turn_id,
                                "status": "failed",
                                "error": {"message": "mock turn failure for testing"},
                            },
                        },
                    }
                )
                return

            if self.special_commands.is_special_command(input_text):
                await self.special_commands.handle_command_async(input_text)

            if self._turn_interrupted:
                return

            await self._simulate_turn_items(turn_id, input_text)

            if self._turn_interrupted:
                return

            if cmd_upper == REQUEST_APPROVAL:
                await self._send_approval_request()

            self._emit_token_usage()

            self.active_turn_id = None
            self._active_turn_task = None
            self._send(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": self.thread_id,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                }
            )

        except asyncio.CancelledError:
            pass

    async def _handle_turn_steer(self, msg_id: Any, params: Dict[str, Any]):
        input_parts = params.get("input", [])
        input_text = self._extract_text(input_parts)

        cmd_upper = input_text.strip().upper()
        if cmd_upper == STEER_REJECT_NOT_STEERABLE:
            self._next_steer_behavior = STEER_REJECT_NOT_STEERABLE
        elif cmd_upper == STEER_REJECT_INPUT_ERROR:
            self._next_steer_behavior = STEER_REJECT_INPUT_ERROR

        behavior = self._next_steer_behavior
        self._next_steer_behavior = None

        if behavior == STEER_REJECT_NOT_STEERABLE:
            self._send(
                {
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": "Turn is not steerable",
                        "data": {
                            "activeTurnNotSteerable": {
                                "turnKind": "review",
                            },
                        },
                    },
                }
            )
            return

        if behavior == STEER_REJECT_INPUT_ERROR:
            self._send(
                {
                    "id": msg_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid steer input",
                    },
                }
            )
            return

        if not self.active_turn_id:
            self._send(
                {
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": "No active turn",
                        "data": {
                            "activeTurnNotSteerable": {
                                "turnKind": "idle",
                            },
                        },
                    },
                }
            )
            return

        response_text = self.custom_responses.get(
            input_text, f"Mock steered response: {input_text}"
        )
        self._send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "delta": response_text,
                    "itemId": f"msg_{self.active_turn_id}",
                    "threadId": self.thread_id,
                    "turnId": self.active_turn_id,
                },
            }
        )

        self._send({"id": msg_id, "result": {}})

    async def _handle_turn_interrupt(self, msg_id: Any, params: Dict[str, Any]):
        turn_id = self.active_turn_id
        if not turn_id:
            self._send(
                {
                    "id": msg_id,
                    "error": {"code": -32000, "message": "No active turn to interrupt"},
                }
            )
            return

        self._turn_interrupted = True
        self.active_turn_id = None
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()
            try:
                await self._active_turn_task
            except asyncio.CancelledError:
                pass
        self._active_turn_task = None

        self._send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": self.thread_id,
                    "turn": {"id": turn_id, "status": "interrupted"},
                },
            }
        )
        self._send({"id": msg_id, "result": {}})

    async def _simulate_turn_items(self, turn_id: str, input_text: str):
        response_text = self.custom_responses.get(
            input_text, f"Mock Codex response: {input_text}"
        )

        if input_text:
            log_task_completion(input_text)

        item_id = f"msg_{turn_id}"
        reasoning_id = f"reasoning_{turn_id}"

        self._send(
            {
                "method": "item/started",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": reasoning_id,
                        "type": "reasoning",
                        "turnId": turn_id,
                        "status": "in_progress",
                        "content": [],
                    },
                },
            }
        )

        self._send(
            {
                "method": "item/reasoning/textDelta",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": turn_id,
                    "itemId": reasoning_id,
                    "delta": "Thinking about the request...",
                    "contentIndex": 0,
                },
            }
        )

        self._send(
            {
                "method": "item/completed",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": reasoning_id,
                        "type": "reasoning",
                        "turnId": turn_id,
                        "status": "completed",
                        "content": ["Thinking about the request..."],
                        "summary": ["Considered the problem"],
                    },
                },
            }
        )

        chunks = _chunk_text(response_text, chunk_size=40)
        for chunk in chunks:
            self._send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "delta": chunk,
                        "itemId": item_id,
                        "threadId": self.thread_id,
                        "turnId": turn_id,
                    },
                }
            )

        self._send(
            {
                "method": "item/started",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": item_id,
                        "type": "agentMessage",
                        "turnId": turn_id,
                        "text": response_text,
                    },
                },
            }
        )

        self._send(
            {
                "method": "item/completed",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": turn_id,
                    "item": {
                        "id": item_id,
                        "type": "agentMessage",
                        "turnId": turn_id,
                        "text": response_text,
                    },
                },
            }
        )

    def _emit_token_usage(self):
        """Emit thread/tokenUsage/updated notification with mock counts.

        Uses the nested format matching real Codex: last (per-turn), total
        (cumulative), and modelContextWindow.
        """
        turn_input = 150
        turn_output = 75
        self.total_input_tokens += turn_input
        self.total_output_tokens += turn_output
        self._send(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": self.active_turn_id,
                    "tokenUsage": {
                        "last": {
                            "totalTokens": turn_input + turn_output,
                            "inputTokens": turn_input,
                            "outputTokens": turn_output,
                        },
                        "total": {
                            "totalTokens": self.total_input_tokens
                            + self.total_output_tokens,
                            "inputTokens": self.total_input_tokens,
                            "outputTokens": self.total_output_tokens,
                        },
                        "modelContextWindow": 128000,
                    },
                },
            }
        )

    async def _send_approval_request(self):
        request_id = f"approval-{uuid.uuid4().hex[:8]}"
        self._send(
            {
                "id": request_id,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": self.thread_id,
                    "turnId": self.active_turn_id or "unknown",
                    "command": "rm -rf /tmp/test",
                },
            }
        )

        future = asyncio.Future()
        self.pending_requests[request_id] = future
        try:
            await asyncio.wait_for(future, timeout=5.0)
        except asyncio.TimeoutError:
            print(
                "Codex mock: approval request timed out waiting for response",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _send(self, msg: Dict[str, Any]):
        """Write a JSON message to stdout (Codex protocol -- no jsonrpc field)."""
        print(json.dumps(msg), flush=True)

    @staticmethod
    def _extract_text(parts: List[Dict[str, Any]]) -> str:
        text = ""
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                text += part.get("text", "")
        return text


def _chunk_text(text: str, chunk_size: int = 40) -> List[str]:
    if not text:
        return [""]
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i : i + chunk_size])
    return chunks


def start_codex_mode(custom_responses: Dict[str, str] = None):
    handler = CodexHandler(custom_responses)
    asyncio.run(handler.run())
