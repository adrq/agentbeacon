"""ACP mode handler for JSON-RPC over stdio communication."""

import asyncio
import json
import sys
from typing import Any, Dict

from .task_store import TaskStore
from .jsonrpc import JSONRPCDispatcher
from .file_logger import log_task_completion

# Polling interval for checking cancellation flags during command execution
CANCELLATION_POLL_INTERVAL = 0.1


class ACPHandler:
    """Handler for ACP (Agent Client Protocol) mode communication."""

    def __init__(self, custom_responses: Dict[str, str] = None):
        self.task_store = TaskStore()
        self.jsonrpc_dispatcher = JSONRPCDispatcher(self.task_store, custom_responses)
        self.custom_responses = custom_responses or {}
        self.active_prompts = {}

    async def run(self):
        """Main ACP processing loop with async I/O."""
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        # We use sys.stdin.buffer (not sys.stdin) because JSON-RPC messages are newline-delimited
        # and must be read as raw bytes before UTF-8 decoding. This prevents Python's text mode
        # from buffering across newlines or applying encoding transformations.
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
                    request = json.loads(line_str)
                    await self._handle_request(request)
                except json.JSONDecodeError:
                    error_response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                    print(json.dumps(error_response), flush=True)
        except (EOFError, KeyboardInterrupt):
            pass

    async def _handle_request(self, request: Dict[str, Any]):
        """Handle incoming request/notification asynchronously."""
        method = request.get("method")

        if method == "session/cancel":
            await self._handle_cancel(request)
        elif method == "session/prompt":
            await self._handle_prompt(request)
        else:
            response = self.jsonrpc_dispatcher.handle_request(request)
            if response is not None:
                print(json.dumps(response), flush=True)

    async def _handle_cancel(self, request: Dict[str, Any]):
        """Handle session/cancel notification.

        Sets cancellation flag for the specified session. The flag is checked
        by the polling loop in _process_prompt to cancel long-running commands.
        """
        params = request.get("params", {})
        session_id = params.get("sessionId")

        if session_id and session_id in self.active_prompts:
            self.active_prompts[session_id]["cancelled"] = True

    async def _handle_prompt(self, request: Dict[str, Any]):
        """Handle session/prompt by spawning background task.

        Validates params and sessionId before spawning. Emits -32602 error
        if validation fails.
        """
        params = request.get("params")
        request_id = request.get("id")

        if not isinstance(params, dict):
            if request_id is not None:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "Invalid params"},
                }
                print(json.dumps(error_response), flush=True)
            return

        session_id = params.get("sessionId")
        if not session_id:
            if request_id is not None:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "Invalid params"},
                }
                print(json.dumps(error_response), flush=True)
            return

        # Track session state BEFORE spawning the task to avoid race conditions.
        # The cancel handler (_handle_cancel) may fire immediately after the task starts,
        # so the session entry must exist before create_task() to ensure cancellation works.
        self.active_prompts[session_id] = {
            "request_id": request_id,
            "cancelled": False,
            "task": None,
        }

        task = asyncio.create_task(self._process_prompt(session_id, request))
        self.active_prompts[session_id]["task"] = task

    async def _process_prompt(self, session_id: str, request: Dict[str, Any]):
        """Process prompt asynchronously with cancellation support."""
        request_id = request.get("id")
        params = request.get("params", {})

        try:
            prompt_parts = params.get("prompt", [])
            text_content = ""
            for part in prompt_parts:
                if part.get("type") == "text":
                    text_content += part.get("text", "")

            special = self.jsonrpc_dispatcher.special_commands

            if text_content.strip().upper() == "INVALID_JSONRPC":
                if request_id is not None:
                    response = {"this_is": "invalid", "missing": "jsonrpc_fields"}
                    print(json.dumps(response), flush=True)
                return

            should_notify = True
            if special.is_special_command(text_content):
                should_notify = text_content.strip().upper() == "STREAM_CHUNKS"

            stop_reason = "end_turn"

            if special.is_special_command(text_content):
                if not self.active_prompts[session_id]["cancelled"]:
                    if should_notify:
                        self._send_notification(request, special)

                try:
                    cmd_task = asyncio.create_task(
                        self.jsonrpc_dispatcher.special_commands.handle_command_async(
                            text_content
                        )
                    )

                    # Poll for cancellation during command execution because asyncio.sleep/time.sleep
                    # can't be interrupted externally. We check the flag every 100ms and cancel the
                    # task if requested. This is the only way to support mid-execution cancellation
                    # for long-running DELAY_N or HANG commands.
                    while not cmd_task.done():
                        if self.active_prompts[session_id]["cancelled"]:
                            cmd_task.cancel()
                            try:
                                await cmd_task
                            except asyncio.CancelledError:
                                pass
                            stop_reason = "cancelled"
                            break
                        await asyncio.sleep(CANCELLATION_POLL_INTERVAL)
                    else:
                        await cmd_task
                        stop_reason = "end_turn"

                except asyncio.CancelledError:
                    stop_reason = "cancelled"
            else:
                response = self.jsonrpc_dispatcher.handle_request(request)

                if not self.active_prompts[session_id]["cancelled"]:
                    if response and "result" in response:
                        if should_notify:
                            self._send_notification(request, special)

                if self.active_prompts[session_id]["cancelled"]:
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"stopReason": "cancelled"},
                    }

                if response and request_id is not None:
                    print(json.dumps(response), flush=True)
                    return

            if self.active_prompts[session_id]["cancelled"]:
                stop_reason = "cancelled"

            if request_id is not None:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"stopReason": stop_reason},
                }
                print(json.dumps(response), flush=True)

        except asyncio.CancelledError:
            if request_id is not None:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"stopReason": "cancelled"},
                }
                print(json.dumps(response), flush=True)

        finally:
            if session_id in self.active_prompts:
                del self.active_prompts[session_id]

    def _send_notification(self, original_request: Dict[str, Any], special_commands):
        """Send session update notification for ACP session/prompt.

        Args:
            original_request: The original session/prompt request
            special_commands: Shared SpecialCommands instance
        """
        params = original_request.get("params", {})
        session_id = params.get("sessionId")

        if session_id:
            prompt_parts = params.get("prompt", [])
            prompt_text = ""
            for part in prompt_parts:
                if part.get("type") == "text":
                    prompt_text += part.get("text", "")

            if special_commands.is_special_command(prompt_text):
                if prompt_text.strip().upper() == "STREAM_CHUNKS":
                    for i in range(3):
                        notification = {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {
                                        "type": "text",
                                        "text": f"Chunk {i + 1}",
                                    },
                                },
                            },
                        }
                        print(json.dumps(notification), flush=True)
                return

            if prompt_text in self.custom_responses:
                response_text = self.custom_responses[prompt_text]
            else:
                response_text = f"Mock ACP response: {prompt_text}"

            if prompt_text:
                log_task_completion(prompt_text)

            notification = {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": response_text},
                    },
                },
            }
            print(json.dumps(notification), flush=True)


def start_acp_mode(custom_responses: Dict[str, str] = None):
    """Start ACP mode handler (async)."""
    handler = ACPHandler(custom_responses)
    asyncio.run(handler.run())  # Run async event loop
