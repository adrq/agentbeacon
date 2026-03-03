"""ACP mode handler for JSON-RPC over stdio communication."""

import asyncio
import json
import os
import signal
import sys
import traceback
from typing import Any, Dict

from .task_store import TaskStore
from .jsonrpc import JSONRPCDispatcher
from .coordination_scenarios import (
    DelegateAskScenario,
    DelegateMultiScenario,
    DelegateReleaseScenario,
    DelegateScenario,
    EndTurnScenario,
)
from .demo_scenario import DemoScenario
from .showcase_scenario import ShowcaseScenario
from .file_logger import log_task_completion

CANCELLATION_POLL_INTERVAL = 0.1


class ACPHandler:
    """Handler for ACP (Agent Client Protocol) mode communication."""

    def __init__(
        self,
        custom_responses: Dict[str, str] = None,
        protocol_version: int = 1,
        hang_initialize: bool = False,
        scenario: str = None,
        delegate_to: str = None,
        delegate_count: int = 2,
    ):
        self.task_store = TaskStore()
        self.jsonrpc_dispatcher = JSONRPCDispatcher(
            self.task_store,
            custom_responses,
            protocol_version=protocol_version,
            hang_initialize=hang_initialize,
        )
        self.custom_responses = custom_responses or {}
        self.scenario = scenario
        self.delegate_to = delegate_to
        self.delegate_count = delegate_count
        self.active_scenario = None
        self.active_prompts = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}

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
                    try:
                        await self._handle_request(request)
                    except Exception:
                        traceback.print_exc(file=sys.stderr)
                except json.JSONDecodeError:
                    error_response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                    print(json.dumps(error_response), flush=True)
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            self._save_captured_messages()

    async def _handle_request(self, request: Dict[str, Any]):
        """Handle incoming request/notification/response asynchronously."""
        # Check if this is a response (has result or error, and id)
        if ("result" in request or "error" in request) and "id" in request:
            await self._handle_response(request)
            return

        method = request.get("method")

        if method == "session/cancel":
            await self._handle_cancel(request)
        elif method == "session/prompt":
            await self._handle_prompt(request)
        else:
            response = self.jsonrpc_dispatcher.handle_request(request)
            if response is not None:
                print(json.dumps(response), flush=True)

    async def _handle_response(self, response: Dict[str, Any]):
        """Handle incoming response to agent-initiated requests."""
        request_id = response.get("id")
        if request_id and request_id in self.pending_requests:
            future = self.pending_requests.pop(request_id)
            if not future.done():
                future.set_result(response)

    def _save_captured_messages(self):
        """Save captured protocol messages to temp file for test verification."""
        try:
            captured_data = {
                "initialize_calls": self.jsonrpc_dispatcher.captured_initialize_calls,
                "session_new_calls": self.jsonrpc_dispatcher.captured_session_new_calls,
            }
            output_path = "/tmp/mock_agent_captured_messages.json"
            with open(output_path, "w") as f:
                json.dump(captured_data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save captured messages: {e}", file=sys.stderr)

    def _create_scenario(self, session_id: str):
        mcp_client = self.jsonrpc_dispatcher.mcp_client
        scenarios = {
            "demo": lambda: DemoScenario(session_id, mcp_client),
            "showcase": lambda: ShowcaseScenario(session_id, mcp_client),
            "delegate": lambda: DelegateScenario(
                session_id, mcp_client, self.delegate_to
            ),
            "delegate-ask": lambda: DelegateAskScenario(
                session_id, mcp_client, self.delegate_to
            ),
            "delegate-multi": lambda: DelegateMultiScenario(
                session_id, mcp_client, self.delegate_to, self.delegate_count
            ),
            "end-turn": lambda: EndTurnScenario(session_id, mcp_client),
            "delegate-release": lambda: DelegateReleaseScenario(
                session_id, mcp_client, self.delegate_to
            ),
        }
        factory = scenarios.get(self.scenario)
        if factory is None:
            raise ValueError(f"Unknown scenario: {self.scenario}")
        return factory()

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

        # Validate session exists
        if session_id not in self.jsonrpc_dispatcher.acp_sessions:
            if request_id is not None:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "Invalid session"},
                }
                print(json.dumps(error_response), flush=True)
            return

        # Lazily initialize scenario on first prompt
        if self.scenario and self.active_scenario is None:
            self.active_scenario = self._create_scenario(session_id)

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

            # Scenario overrides normal dispatch
            if self.active_scenario is not None:
                stop_reason = await self.active_scenario.handle_prompt(text_content)
                if request_id is not None:
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"stopReason": stop_reason},
                    }
                    print(json.dumps(response), flush=True)
                return

            special = self.jsonrpc_dispatcher.special_commands

            if text_content.strip().upper() == "INVALID_JSONRPC":
                if request_id is not None:
                    response = {"this_is": "invalid", "missing": "jsonrpc_fields"}
                    print(json.dumps(response), flush=True)
                return

            should_notify = True
            if special.is_special_command(text_content):
                cmd_upper = text_content.strip().upper()
                should_notify = cmd_upper in [
                    "STREAM_CHUNKS",
                    "REQUEST_PERMISSION",
                    "SEND_PLAN",
                    "SEND_TOOL_CALL",
                    "SEND_MODE_UPDATE",
                    "SEND_COMMANDS_UPDATE",
                    "SEND_MARKDOWN",
                    "SEND_THOUGHT",
                    "SEND_TOOL_CALL_UPDATE",
                    "SEND_TOOL_GROUP",
                    "SEND_TOOL_STREAM",
                ]

            stop_reason = "end_turn"

            if special.is_special_command(text_content):
                cmd_upper = text_content.strip().upper()
                if cmd_upper in ["FAIL_NODE", "EXIT_1"]:
                    os.kill(os.getpid(), signal.SIGKILL)

                if not self.active_prompts[session_id]["cancelled"]:
                    if should_notify:
                        await self._send_notification(request, special)

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
                    if should_notify:
                        await self._send_notification(request, special)

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

    async def _send_notification(
        self, original_request: Dict[str, Any], special_commands
    ):
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
                elif prompt_text.strip().upper() == "REQUEST_PERMISSION":
                    request_id = f"perm-{session_id}"
                    permission_request = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": session_id,
                            "toolCallId": "test-tool-123",
                            "toolCall": {
                                "toolCallId": "test-tool-123",
                                "title": "Test tool",
                                "kind": "read",
                            },
                            "options": [
                                {
                                    "optionId": "allow-once",
                                    "name": "Allow once",
                                    "kind": "allow_once",
                                },
                                {
                                    "optionId": "deny",
                                    "name": "Deny",
                                    "kind": "reject_once",
                                },
                            ],
                        },
                    }

                    future = asyncio.Future()
                    self.pending_requests[request_id] = future

                    print(json.dumps(permission_request), flush=True)

                    try:
                        _response = await asyncio.wait_for(future, timeout=5.0)
                    except asyncio.TimeoutError:
                        pass

                    return
                elif prompt_text.strip().upper() == "SEND_PLAN":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "plan",
                                "entries": [
                                    {
                                        "id": "step-1",
                                        "title": "Analyze requirements",
                                        "status": "completed",
                                    },
                                    {
                                        "id": "step-2",
                                        "title": "Implement feature",
                                        "status": "in_progress",
                                    },
                                ],
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_TOOL_CALL":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": "tool-123",
                                "title": "Read file config.json",
                                "content": [
                                    {"type": "text", "text": "file contents here"}
                                ],
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_MODE_UPDATE":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "current_mode_update",
                                "currentModeId": "code",
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_COMMANDS_UPDATE":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "available_commands_update",
                                "availableCommands": [
                                    {"name": "/test", "description": "Run tests"}
                                ],
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_MARKDOWN":
                    markdown_text = (
                        "# Analysis Report\n\n"
                        "## Summary\n\n"
                        "The implementation looks **solid** with a few notes:\n\n"
                        "| Component | Status | Notes |\n"
                        "|-----------|--------|-------|\n"
                        "| Auth | Done | JWT with refresh tokens |\n"
                        "| API | In Progress | 3 endpoints remaining |\n\n"
                        "### Code Example\n\n"
                        "```python\ndef authenticate(token: str) -> User:\n"
                        "    payload = jwt.decode(token, SECRET_KEY)\n"
                        "    return User.from_payload(payload)\n```\n\n"
                        "- First item in list\n"
                        "- Second item with **bold**\n\n"
                        "> Important: Review the token expiry settings before deploy."
                    )
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {
                                    "type": "text",
                                    "text": markdown_text,
                                },
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_THOUGHT":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_thought_chunk",
                                "content": {
                                    "type": "text",
                                    "text": "I need to analyze the code structure first...",
                                },
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_TOOL_CALL_UPDATE":
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": "tool-456",
                                "title": "Running tests",
                                "status": "completed",
                            },
                        },
                    }
                    print(json.dumps(notification), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_TOOL_GROUP":
                    notification1 = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": "toolgroup-read",
                                "title": "Read config.json",
                                "content": [{"type": "text", "text": '{"port": 8080}'}],
                            },
                        },
                    }
                    print(json.dumps(notification1), flush=True)
                    notification2 = {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": "toolgroup-read",
                                "title": "Read config.json",
                                "status": "completed",
                            },
                        },
                    }
                    print(json.dumps(notification2), flush=True)
                    return
                elif prompt_text.strip().upper() == "SEND_TOOL_STREAM":
                    sid = session_id[:8]
                    tools = [
                        (
                            f"ts-ws-1-{sid}",
                            "WebSearch",
                            'query: "Rust async patterns 2026"',
                        ),
                        (
                            f"ts-ws-2-{sid}",
                            "WebSearch",
                            'query: "tokio vs async-std comparison"',
                        ),
                        (
                            f"ts-ws-3-{sid}",
                            "WebSearch",
                            'query: "Rust error handling best practices"',
                        ),
                        (
                            f"ts-ws-4-{sid}",
                            "WebSearch",
                            'query: "serde json performance tips"',
                        ),
                        (f"ts-wf-1-{sid}", "WebFetch", "https://docs.rs/tokio/latest"),
                        (f"ts-rd-1-{sid}", "Read", "src/lib.rs"),
                    ]
                    for tool_id, title, content_text in tools:
                        print(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "session/update",
                                    "params": {
                                        "sessionId": session_id,
                                        "update": {
                                            "sessionUpdate": "tool_call",
                                            "toolCallId": tool_id,
                                            "title": title,
                                            "content": [
                                                {"type": "text", "text": content_text}
                                            ],
                                        },
                                    },
                                }
                            ),
                            flush=True,
                        )
                        print(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "session/update",
                                    "params": {
                                        "sessionId": session_id,
                                        "update": {
                                            "sessionUpdate": "tool_call_update",
                                            "toolCallId": tool_id,
                                            "title": title,
                                            "status": "completed",
                                        },
                                    },
                                }
                            ),
                            flush=True,
                        )
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


def start_acp_mode(
    custom_responses: Dict[str, str] = None,
    protocol_version: int = 1,
    hang_initialize: bool = False,
    scenario: str = None,
    delegate_to: str = None,
    delegate_count: int = 2,
):
    """Start ACP mode handler (async)."""
    handler = ACPHandler(
        custom_responses,
        protocol_version,
        hang_initialize,
        scenario=scenario,
        delegate_to=delegate_to,
        delegate_count=delegate_count,
    )
    asyncio.run(handler.run())
