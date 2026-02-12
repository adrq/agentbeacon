"""JSON-RPC request dispatcher for A2A and ACP protocols."""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, List

from pydantic import ValidationError

from a2a.types import Message, TextPart, FilePart, DataPart, Part, Role, Task
from a2a.utils import new_text_artifact

from .task_store import TaskStore
from .special_commands import SpecialCommands
from .file_logger import log_task_completion
from .mcp_client import McpClient


class JSONRPCDispatcher:
    """Handles JSON-RPC requests for both A2A and ACP protocols."""

    def __init__(
        self,
        task_store: TaskStore,
        custom_responses: Dict[str, str] = None,
        protocol_version: int = 1,
        hang_initialize: bool = False,
    ):
        self.task_store = task_store
        self.special_commands = SpecialCommands()
        self.custom_responses = custom_responses or {}
        self.acp_sessions: Dict[str, dict] = {}
        self.acp_initialized = False
        self.protocol_version = protocol_version
        self.hang_initialize = hang_initialize
        self.captured_initialize_calls: list = []
        self.captured_session_new_calls: list = []
        self.mcp_client: Optional[McpClient] = None

    def _serialize_task(
        self, task: Task, history_length: Optional[int] = None
    ) -> Dict[str, Any]:
        """Serialize task with proper enum handling and optional history limiting.

        Args:
            task: The task to serialize
            history_length: Limit history to most recent N messages

        Returns serialized task dict with enums as strings.
        """
        serialized = task.model_dump(mode="json", exclude_none=True)

        if history_length is not None and "history" in serialized:
            history = serialized["history"]
            if len(history) > history_length:
                serialized["history"] = history[-history_length:]

        return serialized

    def _validate_and_parse_message(
        self, msg_data: Any, request_id: Any
    ) -> tuple[Optional[Message], Optional[Dict[str, Any]]]:
        """Validate and parse message data, returning (message, error_response).

        Returns (Message, None) on success, or (None, error_response_dict) on failure.
        """
        try:
            # Validate message is a dict
            if not isinstance(msg_data, dict):
                return None, self._error_response(
                    request_id, -32602, "Invalid params: 'message' must be an object"
                )

            # Validate required fields exist
            if "role" not in msg_data:
                return None, self._error_response(
                    request_id, -32602, "Invalid params: 'message.role' is required"
                )
            if "parts" not in msg_data:
                return None, self._error_response(
                    request_id, -32602, "Invalid params: 'message.parts' is required"
                )

            role_str = msg_data["role"]
            if role_str not in ("user", "agent"):
                return None, self._error_response(
                    request_id,
                    -32602,
                    f"Invalid params: message.role must be 'user' or 'agent', got '{role_str}'",
                )

            parts: List[Part] = []
            parts_data = msg_data.get("parts", [])
            if not isinstance(parts_data, list) or len(parts_data) == 0:
                return None, self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: 'message.parts' must be a non-empty array",
                )

            for i, part_data in enumerate(parts_data):
                if not isinstance(part_data, dict):
                    return None, self._error_response(
                        request_id,
                        -32602,
                        f"Invalid params: message.parts[{i}] must be an object",
                    )

                part_kind = part_data.get("kind")
                if not part_kind:
                    return None, self._error_response(
                        request_id,
                        -32602,
                        f"Invalid params: message.parts[{i}].kind is required",
                    )

                if part_kind == "text":
                    if "text" not in part_data:
                        return None, self._error_response(
                            request_id,
                            -32602,
                            f"Invalid params: message.parts[{i}].text is required for text parts",
                        )
                    parts.append(
                        TextPart(
                            text=part_data["text"], metadata=part_data.get("metadata")
                        )
                    )
                elif part_kind == "file":
                    if "file" not in part_data:
                        return None, self._error_response(
                            request_id,
                            -32602,
                            f"Invalid params: message.parts[{i}].file is required for file parts",
                        )
                    parts.append(
                        FilePart(
                            file=part_data["file"], metadata=part_data.get("metadata")
                        )
                    )
                elif part_kind == "data":
                    if "data" not in part_data:
                        return None, self._error_response(
                            request_id,
                            -32602,
                            f"Invalid params: message.parts[{i}].data is required for data parts",
                        )
                    parts.append(
                        DataPart(
                            data=part_data["data"], metadata=part_data.get("metadata")
                        )
                    )
                else:
                    return None, self._error_response(
                        request_id,
                        -32602,
                        f"Invalid params: message.parts[{i}].kind must be 'text', 'file', or 'data', got '{part_kind}'",
                    )

            if "messageId" not in msg_data:
                return None, self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: 'message.messageId' is required",
                )
            message_id = msg_data["messageId"]
            if not isinstance(message_id, str) or not message_id.strip():
                return None, self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: 'message.messageId' must be a non-empty string",
                )

            # Per A2A protocol: contextId is optional on incoming messages but required in Task.
            # Generate a UUID if client omits it to maintain spec compliance in the task store.
            context_id = msg_data.get("contextId", str(uuid.uuid4()))

            message = Message(
                messageId=message_id,
                role=Role(role_str),
                parts=parts,
                contextId=context_id,
                taskId=msg_data.get("taskId"),
                metadata=msg_data.get("metadata"),
                extensions=msg_data.get("extensions"),
                referenceTaskIds=msg_data.get("referenceTaskIds"),
            )

            return message, None

        except ValidationError as e:
            return None, self._error_response(
                request_id, -32602, f"Invalid params: {str(e)}"
            )
        except (KeyError, TypeError) as e:
            return None, self._error_response(
                request_id, -32602, f"Invalid params: {str(e)}"
            )

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process JSON-RPC request and return response."""
        try:
            is_notification = "id" not in request
            method = request.get("method")
            params = request.get("params", {})
            request_id = request.get("id")

            if not is_notification and not self._is_valid_jsonrpc(request):
                return self._error_response(request_id, -32600, "Invalid Request")

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
                # In async ACP mode, session/prompt is handled by _handle_prompt in acp_mode.py
                # Return None to let the async handler take care of it
                return None
            elif method == "session/cancel":
                return None
            else:
                return self._error_response(request_id, -32601, "Method not found")

        except Exception as e:
            return self._error_response(
                request.get("id"), -32603, f"Internal error: {str(e)}"
            )

    async def handle_request_async(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process JSON-RPC request asynchronously."""
        try:
            if not self._is_valid_jsonrpc(request):
                return self._error_response(
                    request.get("id"), -32600, "Invalid Request"
                )

            method = request["method"]
            params = request.get("params", {})
            request_id = request["id"]

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
            if not isinstance(params, dict):
                return self._error_response(
                    request_id, -32602, "Invalid params: params must be an object"
                )

            if "message" not in params:
                return self._error_response(
                    request_id, -32602, "Invalid params: 'message' field required"
                )

            message, error_response = self._validate_and_parse_message(
                params["message"], request_id
            )
            if error_response:
                return error_response

            request_config = params.get("configuration", {})
            _ = params.get("metadata")
            history_length = (
                request_config.get("historyLength") if request_config else None
            )

            if message.task_id:
                task = self.task_store.append_message_to_task(message.task_id, message)
                if not task:
                    existing_task = self.task_store.get_task(message.task_id)
                    if existing_task:
                        return self._error_response(
                            request_id,
                            -32004,
                            f"Task cannot be continued: task is in terminal state '{existing_task.status.state.value}'",
                        )
                    else:
                        return self._error_response(
                            request_id, -32001, f"Task not found: {message.task_id}"
                        )
            else:
                task = self.task_store.create_task_from_message(message)

            first_text = self._extract_first_text([message])
            if first_text:
                if first_text in self.custom_responses:
                    custom_response = self.custom_responses[first_text]
                    self.task_store.set_task_working(task.id)

                    if custom_response == "HANG":
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        artifact = new_text_artifact("agent-output", custom_response)
                        self.task_store.add_task_artifact(task.id, artifact)
                        log_task_completion(first_text)
                        self.task_store.complete_task(task.id)
                elif self.special_commands.is_special_command(first_text):
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(task.id)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        self.task_store.set_task_working(task.id)
                        result = self.special_commands.handle_command(first_text)
                        if result:
                            artifact = new_text_artifact("agent-output", result)
                            self.task_store.add_task_artifact(task.id, artifact)
                            log_task_completion(first_text)
                            self.task_store.complete_task(task.id)
                else:
                    self.task_store.set_task_working(task.id)
                    default_response = f"Mock agent received: {first_text}"
                    artifact = new_text_artifact("agent-output", default_response)
                    self.task_store.add_task_artifact(task.id, artifact)
                    log_task_completion(first_text)
                    self.task_store.complete_task(task.id)
            else:
                self.task_store.set_task_working(task.id)
                artifact = new_text_artifact(
                    "agent-output", "Mock agent processed request"
                )
                self.task_store.add_task_artifact(task.id, artifact)
                self.task_store.complete_task(task.id)

            updated_task = self.task_store.get_task(task.id)
            serialized_task = self._serialize_task(updated_task, history_length)
            return self._success_response(request_id, serialized_task)

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _handle_message_send_async(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A message/send request asynchronously."""
        try:
            if not isinstance(params, dict):
                return self._error_response(
                    request_id, -32602, "Invalid params: params must be an object"
                )

            if "message" not in params:
                return self._error_response(
                    request_id, -32602, "Invalid params: 'message' field required"
                )

            message, error_response = self._validate_and_parse_message(
                params["message"], request_id
            )
            if error_response:
                return error_response

            request_config = params.get("configuration", {})
            _ = params.get("metadata")
            history_length = (
                request_config.get("historyLength") if request_config else None
            )

            if message.task_id:
                task = self.task_store.append_message_to_task(message.task_id, message)
                if not task:
                    existing_task = self.task_store.get_task(message.task_id)
                    if existing_task:
                        return self._error_response(
                            request_id,
                            -32004,
                            f"Task cannot be continued: task is in terminal state '{existing_task.status.state.value}'",
                        )
                    else:
                        return self._error_response(
                            request_id, -32001, f"Task not found: {message.task_id}"
                        )
            else:
                task = self.task_store.create_task_from_message(message)

            first_text = self._extract_first_text([message])

            if first_text:
                if first_text in self.custom_responses:
                    custom_response = self.custom_responses[first_text]
                    self.task_store.set_task_working(task.id)

                    if custom_response == "HANG":
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        artifact = new_text_artifact("agent-output", custom_response)
                        self.task_store.add_task_artifact(task.id, artifact)
                        log_task_completion(first_text)
                        self.task_store.complete_task(task.id)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                elif self.special_commands.is_special_command(first_text):
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(task.id)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    elif first_text.strip().upper() == "FAIL_NODE":
                        self.task_store.fail_task(task.id)

                        failure_message = Message(
                            messageId=f"{task.id}-fail-response",
                            kind="message",
                            role="agent",
                            parts=[
                                TextPart(
                                    kind="text",
                                    text=f"Mock agent failure: {first_text}",
                                )
                            ],
                        )

                        updated_task = self.task_store.get_task(task.id)
                        updated_task.status.message = failure_message

                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        self.task_store.set_task_working(task.id)

                        # Launch background task WITHOUT awaiting to return immediately with task status "working".
                        # The A2A protocol requires immediate response while the agent processes asynchronously.
                        # Task completion will update the task store when handle_command_async finishes.
                        asyncio.create_task(
                            self._process_special_command_async(task.id, first_text)
                        )

                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                else:
                    self.task_store.set_task_working(task.id)
                    default_response = f"Mock agent received: {first_text}"
                    artifact = new_text_artifact("agent-output", default_response)
                    self.task_store.add_task_artifact(task.id, artifact)
                    log_task_completion(first_text)
                    self.task_store.complete_task(task.id)
            else:
                self.task_store.set_task_working(task.id)
                artifact = new_text_artifact(
                    "agent-output", "Mock agent processed request"
                )
                self.task_store.add_task_artifact(task.id, artifact)
                self.task_store.complete_task(task.id)

            updated_task = self.task_store.get_task(task.id)
            serialized_task = self._serialize_task(updated_task, history_length)
            return self._success_response(request_id, serialized_task)

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _process_special_command_async(self, task_id: str, command_text: str):
        """Process special command asynchronously and update task when complete."""
        try:
            result = await self.special_commands.handle_command_async(command_text)
            if result:
                artifact = new_text_artifact("agent-output", result)
                self.task_store.add_task_artifact(task_id, artifact)
                log_task_completion(command_text)
                self.task_store.complete_task(task_id)
        except Exception:
            self.task_store.fail_task(task_id)

    def _handle_tasks_get(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A tasks/get request."""
        if not isinstance(params, dict):
            return self._error_response(
                request_id, -32602, "Invalid params: params must be an object"
            )

        task_id = params.get("id") or params.get("taskId")
        if not task_id:
            return self._error_response(
                request_id, -32602, "Invalid params: 'id' field required"
            )

        task = self.task_store.get_task(task_id)
        if not task:
            return self._error_response(request_id, -32001, "Task not found")

        history_length = params.get("historyLength")
        return self._success_response(
            request_id, self._serialize_task(task, history_length)
        )

    def _handle_tasks_cancel(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A tasks/cancel request."""
        if not isinstance(params, dict):
            return self._error_response(
                request_id, -32602, "Invalid params: params must be an object"
            )

        task_id = params.get("id") or params.get("taskId")
        if not task_id:
            return self._error_response(
                request_id, -32602, "Invalid params: 'id' field required"
            )

        task = self.task_store.cancel_task(task_id)
        if not task:
            return self._error_response(request_id, -32001, "Task not found")

        return self._success_response(request_id, self._serialize_task(task))

    def _handle_acp_initialize(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle ACP initialize request."""
        import time

        self.captured_initialize_calls.append(
            {"params": params.copy(), "request_id": request_id}
        )

        if self.hang_initialize:
            time.sleep(3600)  # Sleep for 1 hour (will be killed by timeout)

        protocol_version = params.get("protocolVersion")
        if protocol_version != 1:
            return self._error_response(request_id, -32602, "Invalid params")

        self.acp_initialized = True

        return self._success_response(
            request_id,
            {
                "protocolVersion": self.protocol_version,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {"embeddedContext": True},
                    "mcpCapabilities": {"http": True, "sse": False},
                },
                "authMethods": [],
            },
        )

    def _handle_acp_session_new(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle ACP session/new request."""
        self.captured_session_new_calls.append(
            {"params": params.copy(), "request_id": request_id}
        )

        if not self.acp_initialized:
            return self._error_response(request_id, -32603, "Internal error")

        session_id = str(uuid.uuid4())
        self.acp_sessions[session_id] = {
            "id": session_id,
            "cwd": params.get("cwd", ""),
            "created": datetime.utcnow().isoformat(),
        }

        # Extract MCP server config if provided
        for server in params.get("mcpServers", []):
            if server.get("type") == "http":
                url = server.get("url", "")
                headers = {}
                for h in server.get("headers", []):
                    name = h.get("name")
                    value = h.get("value")
                    if name and value:
                        headers[name] = value
                self.mcp_client = McpClient(url, headers)
                break

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
        text_content = ""
        for part in prompt_parts:
            if part.get("type") == "text":
                text_content += part.get("text", "")

        if text_content in self.custom_responses:
            custom_response = self.custom_responses[text_content]
            if custom_response == "HANG":
                import time

                time.sleep(3600)
        elif self.special_commands.is_special_command(text_content):
            result = self.special_commands.handle_command(text_content)

            if result == "INVALID_JSONRPC":
                return {"this_is": "invalid", "missing": "jsonrpc_fields"}
            elif result == "STREAM_CHUNKS":
                pass

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
