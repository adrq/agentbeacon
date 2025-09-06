"""JSON-RPC request dispatcher for A2A and ACP protocols."""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from a2a.types import Message, TextPart, Role
from a2a.utils import new_text_artifact

from .task_store import TaskStore
from .special_commands import SpecialCommands


class JSONRPCDispatcher:
    """Handles JSON-RPC requests for both A2A and ACP protocols."""

    def __init__(self, task_store: TaskStore, custom_responses: Dict[str, str] = None):
        self.task_store = task_store
        self.special_commands = SpecialCommands()
        self.custom_responses = custom_responses or {}
        self.acp_sessions: Dict[str, dict] = {}
        self.acp_initialized = False

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process JSON-RPC request and return response (synchronous version)."""
        try:
            # Validate basic JSON-RPC structure
            if not self._is_valid_jsonrpc(request):
                return self._error_response(
                    request.get("id"), -32600, "Invalid Request"
                )

            method = request["method"]
            params = request.get("params", {})
            request_id = request["id"]

            # Dispatch to appropriate handler
            if method == "message/send":
                return self._handle_message_send_sync(request_id, params)
            elif method == "tasks/get":
                return self._handle_tasks_get(request_id, params)
            elif method == "tasks/cancel":
                return self._handle_tasks_cancel(request_id, params)
            elif method == "initialize":
                return self._handle_acp_initialize(request_id, params)
            elif method == "session/new":
                return self._handle_acp_session_new(request_id, params)
            elif method == "session/prompt":
                return self._handle_acp_session_prompt(request_id, params)
            else:
                return self._error_response(request_id, -32601, "Method not found")

        except Exception as e:
            return self._error_response(
                request.get("id"), -32603, f"Internal error: {str(e)}"
            )

    async def handle_request_async(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process JSON-RPC request and return response (async version for A2A)."""
        try:
            # Validate basic JSON-RPC structure
            if not self._is_valid_jsonrpc(request):
                return self._error_response(
                    request.get("id"), -32600, "Invalid Request"
                )

            method = request["method"]
            params = request.get("params", {})
            request_id = request["id"]

            # Dispatch to appropriate handler
            if method == "message/send":
                return await self._handle_message_send_async(request_id, params)
            elif method == "tasks/get":
                return self._handle_tasks_get(request_id, params)
            elif method == "tasks/cancel":
                return self._handle_tasks_cancel(request_id, params)
            else:
                return self._error_response(request_id, -32601, "Method not found")

        except Exception as e:
            return self._error_response(
                request.get("id"), -32603, f"Internal error: {str(e)}"
            )

    def _is_valid_jsonrpc(self, request: Dict[str, Any]) -> bool:
        """Validate basic JSON-RPC 2.0 structure."""
        return (
            isinstance(request, dict)
            and request.get("jsonrpc") == "2.0"
            and "method" in request
            and "id" in request
        )

    def _handle_message_send_sync(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A message/send request."""
        try:
            # Validate required parameters
            if "contextId" not in params or "messages" not in params:
                return self._error_response(request_id, -32602, "Invalid params")

            context_id = params["contextId"]
            messages_data = params["messages"]

            # Convert to A2A Message objects with proper structure
            messages = []
            for msg_data in messages_data:
                parts = []
                for part_data in msg_data.get("parts", []):
                    if part_data.get("kind") == "text":
                        parts.append(TextPart(text=part_data["text"]))

                # Create proper Message with all required fields
                message = Message(
                    messageId=str(uuid.uuid4()),
                    role=Role(msg_data["role"]),
                    parts=parts,
                    contextId=context_id,
                )
                messages.append(message)

            # Create task using the first message (A2A pattern)
            if not messages:
                return self._error_response(request_id, -32602, "No messages provided")

            task = self.task_store.create_task_from_message(messages[0])

            # Check for custom responses and special commands in the first text part
            first_text = self._extract_first_text(messages)
            if first_text:
                # Check custom responses first
                if first_text in self.custom_responses:
                    custom_response = self.custom_responses[first_text]
                    self.task_store.set_task_working(task.id)

                    # Handle special HANG value in config
                    if custom_response == "HANG":
                        # Return task in working state, don't complete it (will hang)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                    else:
                        # Complete with custom response
                        artifact = new_text_artifact("agent-output", custom_response)
                        self.task_store.add_task_artifact(task.id, artifact)
                        self.task_store.complete_task(task.id)
                # Check for special commands
                elif self.special_commands.is_special_command(first_text):
                    # For HANG commands, return task in working state without completing
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(task.id)
                        # Return task in working state, don't complete it
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                    else:
                        # For other special commands, handle and complete immediately
                        self.task_store.set_task_working(task.id)
                        result = self.special_commands.handle_command(first_text)
                        if result:
                            artifact = new_text_artifact("agent-output", result)
                            self.task_store.add_task_artifact(task.id, artifact)
                            self.task_store.complete_task(task.id)
                else:
                    # Regular messages: return in submitted state per test expectations
                    # Don't process immediately, just return submitted task
                    pass
            else:
                # No text content: return in submitted state
                pass

            # Get task and serialize with model_dump
            updated_task = self.task_store.get_task(task.id)
            return self._success_response(request_id, updated_task.model_dump())

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _handle_message_send_async(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A message/send request (async version for proper delay handling)."""
        try:
            # Validate required parameters
            if "contextId" not in params or "messages" not in params:
                return self._error_response(request_id, -32602, "Invalid params")

            context_id = params["contextId"]
            messages_data = params["messages"]

            # Convert to A2A Message objects with proper structure
            messages = []
            for msg_data in messages_data:
                parts = []
                for part_data in msg_data.get("parts", []):
                    if part_data.get("kind") == "text":
                        parts.append(TextPart(text=part_data["text"]))

                # Create proper Message with all required fields
                message = Message(
                    messageId=str(uuid.uuid4()),
                    role=Role(msg_data["role"]),
                    parts=parts,
                    contextId=context_id,
                )
                messages.append(message)

            # Create task using the first message (A2A pattern)
            if not messages:
                return self._error_response(request_id, -32602, "No messages provided")

            task = self.task_store.create_task_from_message(messages[0])

            # Check for custom responses and special commands in the first text part
            first_text = self._extract_first_text(messages)

            if first_text:
                # Check custom responses first
                if first_text in self.custom_responses:
                    custom_response = self.custom_responses[first_text]
                    self.task_store.set_task_working(task.id)

                    # Handle special HANG value in config
                    if custom_response == "HANG":
                        # Return task in working state, don't complete it (will hang)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                    else:
                        # Complete with custom response
                        artifact = new_text_artifact("agent-output", custom_response)
                        self.task_store.add_task_artifact(task.id, artifact)
                        self.task_store.complete_task(task.id)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                # Check for special commands
                elif self.special_commands.is_special_command(first_text):
                    # For HANG commands, return task in working state without completing
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(task.id)
                        # Return task in working state, don't complete it
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                    elif first_text.strip().upper() == "FAIL_NODE":
                        # For FAIL_NODE, immediately mark as failed
                        self.task_store.fail_task(task.id)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                    else:
                        # For other special commands, handle async and complete
                        self.task_store.set_task_working(task.id)

                        # Start async processing of the command
                        asyncio.create_task(
                            self._process_special_command_async(task.id, first_text)
                        )

                        # Return task in working state immediately
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, updated_task.model_dump()
                        )
                else:
                    # Regular messages: return in submitted state per test expectations
                    # Don't process immediately, just return submitted task
                    pass
            else:
                # No text content: return in submitted state
                pass

            # Get task and serialize with model_dump
            updated_task = self.task_store.get_task(task.id)
            return self._success_response(request_id, updated_task.model_dump())

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _process_special_command_async(self, task_id: str, command_text: str):
        """Process special command asynchronously and update task when complete."""
        try:
            result = await self.special_commands.handle_command_async(command_text)
            if result:
                artifact = new_text_artifact("agent-output", result)
                self.task_store.add_task_artifact(task_id, artifact)
                self.task_store.complete_task(task_id)
        except Exception:
            # Mark task as failed if command processing fails
            self.task_store.fail_task(task_id)

    def _handle_tasks_get(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A tasks/get request."""
        task_id = params.get("taskId")
        if not task_id:
            return self._error_response(request_id, -32602, "Invalid params")

        task = self.task_store.get_task(task_id)
        if not task:
            return self._error_response(request_id, -32001, "Task not found")

        return self._success_response(request_id, task.model_dump())

    def _handle_tasks_cancel(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A tasks/cancel request."""
        task_id = params.get("taskId")
        if not task_id:
            return self._error_response(request_id, -32602, "Invalid params")

        task = self.task_store.cancel_task(task_id)
        if not task:
            return self._error_response(request_id, -32001, "Task not found")

        return self._success_response(request_id, task.model_dump())

    def _handle_acp_initialize(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle ACP initialize request."""
        # Validate protocol version
        protocol_version = params.get("protocolVersion")
        if protocol_version != 1:
            return self._error_response(request_id, -32602, "Invalid params")

        # Mark as initialized
        self.acp_initialized = True

        return self._success_response(
            request_id,
            {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {"embeddedContext": True},
                    "mcpCapabilities": {"http": False, "sse": False},
                },
                "authMethods": [],
            },
        )

    def _handle_acp_session_new(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle ACP session/new request."""
        # Check if initialized
        if not self.acp_initialized:
            return self._error_response(request_id, -32603, "Internal error")

        session_id = str(uuid.uuid4())
        self.acp_sessions[session_id] = {
            "id": session_id,
            "cwd": params.get("cwd", ""),
            "created": datetime.utcnow().isoformat(),
        }

        return self._success_response(
            request_id, {"sessionId": session_id, "modes": None}
        )

    def _handle_acp_session_prompt(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle ACP session/prompt request."""
        session_id = params.get("sessionId")
        if not session_id or session_id not in self.acp_sessions:
            return self._error_response(request_id, -32602, "Invalid session")

        prompt_parts = params.get("prompt", [])

        # Extract text from prompt
        text_content = ""
        for part in prompt_parts:
            if part.get("type") == "text":
                text_content += part.get("text", "")

        # Check custom responses first, then special commands
        if text_content in self.custom_responses:
            custom_response = self.custom_responses[text_content]
            # Handle special HANG value in config
            if custom_response == "HANG":
                import time

                time.sleep(3600)  # Hang for 1 hour
            # For other custom responses, we just continue (no special handling needed for ACP)
        elif self.special_commands.is_special_command(text_content):
            _ = self.special_commands.handle_command(text_content)
            # Note: Special commands may exit the process

        return self._success_response(request_id, {"stopReason": "end_turn"})

    def _extract_first_text(self, messages: list[Message]) -> Optional[str]:
        """Extract first text content from messages."""
        for message in messages:
            for part in message.parts:
                if hasattr(part, "root") and hasattr(part.root, "text"):
                    return part.root.text
        return None

    def _success_response(self, request_id: Any, result: Any) -> Dict[str, Any]:
        """Create JSON-RPC success response."""
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_response(
        self, request_id: Any, code: int, message: str
    ) -> Dict[str, Any]:
        """Create JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
