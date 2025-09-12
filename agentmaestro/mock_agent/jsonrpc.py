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


class JSONRPCDispatcher:
    """Handles JSON-RPC requests for both A2A and ACP protocols."""

    def __init__(self, task_store: TaskStore, custom_responses: Dict[str, str] = None):
        self.task_store = task_store
        self.special_commands = SpecialCommands()
        self.custom_responses = custom_responses or {}
        self.acp_sessions: Dict[str, dict] = {}
        self.acp_initialized = False

    def _serialize_task(
        self, task: Task, history_length: Optional[int] = None
    ) -> Dict[str, Any]:
        """Serialize task with proper enum handling per §10.

        Uses mode="json" to ensure enums are serialized as strings.
        Pydantic models already validate structure, so no additional schema
        validation needed for Task responses.

        Args:
            task: The task to serialize
            history_length: Per §7.1.1, limit history to most recent N messages

        Returns serialized task dict with proper enum handling.
        """
        # Serialize with mode="json" to convert enums to strings
        serialized = task.model_dump(mode="json", exclude_none=True)

        # Honor historyLength configuration per §7.1.1
        if history_length is not None and "history" in serialized:
            history = serialized["history"]
            if len(history) > history_length:
                # Keep only the most recent history_length messages
                serialized["history"] = history[-history_length:]

        return serialized

    def _validate_and_parse_message(
        self, msg_data: Any, request_id: Any
    ) -> tuple[Optional[Message], Optional[Dict[str, Any]]]:
        """Validate and parse message data per §6.4, returning (message, error_response).

        Returns (Message, None) on success, or (None, error_response_dict) on validation failure.
        Per §8 & §10, invalid params must return -32602, not -32603.
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

            # Validate role is valid enum value per A2A spec §6.4
            # Accept both "user" and "agent" - while clients typically send "user",
            # agents may receive their own messages in history during continuations
            role_str = msg_data["role"]
            if role_str not in ("user", "agent"):
                return None, self._error_response(
                    request_id,
                    -32602,
                    f"Invalid params: message.role must be 'user' or 'agent', got '{role_str}'",
                )

            # Parse parts array, supporting all Part types per §6.5
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

            # Validate messageId is present per §6.4 (line 1127: messageId is required, not optional)
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

            # Extract contextId from message or generate one
            context_id = msg_data.get("contextId", str(uuid.uuid4()))

            # Create proper Message with all required fields including continuation support
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
            # Pydantic validation error - return -32602
            return None, self._error_response(
                request_id, -32602, f"Invalid params: {str(e)}"
            )
        except (KeyError, TypeError) as e:
            # Missing required field or wrong type - return -32602
            return None, self._error_response(
                request_id, -32602, f"Invalid params: {str(e)}"
            )

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
        """Handle A2A message/send request per spec (MessageSendParams)."""
        try:
            # Validate params is a dict per §8 - return -32602 for invalid params type
            if not isinstance(params, dict):
                return self._error_response(
                    request_id, -32602, "Invalid params: params must be an object"
                )

            # Validate required parameters per A2A spec
            if "message" not in params:
                return self._error_response(
                    request_id, -32602, "Invalid params: 'message' field required"
                )

            # Validate and parse message using dedicated validator per §8 & §10
            message, error_response = self._validate_and_parse_message(
                params["message"], request_id
            )
            if error_response:
                return error_response

            # Extract configuration and metadata from MessageSendParams per §7.1.1
            request_config = params.get("configuration", {})
            _ = params.get("metadata")  # Acknowledged but not used by mock agent
            history_length = (
                request_config.get("historyLength") if request_config else None
            )

            # Per §6.4 & §9.2-§9.4: if message.task_id is set, continue existing task
            # Otherwise create new task (note: Pydantic uses snake_case internally)
            if message.task_id:
                task = self.task_store.append_message_to_task(message.task_id, message)
                if not task:
                    # Task not found or in terminal state per §7.1
                    # Check if task exists to distinguish between -32001 and -32004
                    existing_task = self.task_store.get_task(message.task_id)
                    if existing_task:
                        # Task exists but is in terminal state - return -32004 per spec
                        return self._error_response(
                            request_id,
                            -32004,
                            f"Task cannot be continued: task is in terminal state '{existing_task.status.state.value}'",
                        )
                    else:
                        # Task not found - return -32001 per spec
                        return self._error_response(
                            request_id, -32001, f"Task not found: {message.task_id}"
                        )
            else:
                # Create new task from message
                task = self.task_store.create_task_from_message(message)

            # Check for custom responses and special commands in the first text part
            first_text = self._extract_first_text([message])
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
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        # Complete with custom response
                        artifact = new_text_artifact("agent-output", custom_response)
                        self.task_store.add_task_artifact(task.id, artifact)

                        # Log task completion before completing task
                        log_task_completion(first_text)

                        self.task_store.complete_task(task.id)
                # Check for special commands
                elif self.special_commands.is_special_command(first_text):
                    # For HANG commands, return task in working state without completing
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(task.id)
                        # Return task in working state, don't complete it
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        # For other special commands, handle and complete immediately
                        self.task_store.set_task_working(task.id)
                        result = self.special_commands.handle_command(first_text)
                        if result:
                            artifact = new_text_artifact("agent-output", result)
                            self.task_store.add_task_artifact(task.id, artifact)

                            # Log task completion before completing task
                            log_task_completion(first_text)

                            self.task_store.complete_task(task.id)
                else:
                    # Regular messages: for mock agent, complete with default response
                    # Note: Per §7.1, real agents would stay in working/submitted state,
                    # but mock agent completes immediately to support testing workflows
                    self.task_store.set_task_working(task.id)
                    default_response = f"Mock agent received: {first_text}"
                    artifact = new_text_artifact("agent-output", default_response)
                    self.task_store.add_task_artifact(task.id, artifact)
                    log_task_completion(first_text)
                    self.task_store.complete_task(task.id)
            else:
                # No text content: complete with generic response (testing support)
                self.task_store.set_task_working(task.id)
                artifact = new_text_artifact(
                    "agent-output", "Mock agent processed request"
                )
                self.task_store.add_task_artifact(task.id, artifact)
                self.task_store.complete_task(task.id)

            # Get task and serialize, honoring historyLength per §7.1.1
            updated_task = self.task_store.get_task(task.id)
            serialized_task = self._serialize_task(updated_task, history_length)
            return self._success_response(request_id, serialized_task)

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _handle_message_send_async(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A message/send request per spec (async version for proper delay handling)."""
        try:
            # Validate params is a dict per §8 - return -32602 for invalid params type
            if not isinstance(params, dict):
                return self._error_response(
                    request_id, -32602, "Invalid params: params must be an object"
                )

            # Validate required parameters per A2A spec
            if "message" not in params:
                return self._error_response(
                    request_id, -32602, "Invalid params: 'message' field required"
                )

            # Validate and parse message using dedicated validator per §8 & §10
            message, error_response = self._validate_and_parse_message(
                params["message"], request_id
            )
            if error_response:
                return error_response

            # Extract configuration and metadata from MessageSendParams per §7.1.1
            request_config = params.get("configuration", {})
            _ = params.get("metadata")  # Acknowledged but not used by mock agent
            history_length = (
                request_config.get("historyLength") if request_config else None
            )

            # Per §6.4 & §9.2-§9.4: if message.task_id is set, continue existing task
            # Otherwise create new task (note: Pydantic uses snake_case internally)
            if message.task_id:
                task = self.task_store.append_message_to_task(message.task_id, message)
                if not task:
                    # Task not found or in terminal state per §7.1
                    # Check if task exists to distinguish between -32001 and -32004
                    existing_task = self.task_store.get_task(message.task_id)
                    if existing_task:
                        # Task exists but is in terminal state - return -32004 per spec
                        return self._error_response(
                            request_id,
                            -32004,
                            f"Task cannot be continued: task is in terminal state '{existing_task.status.state.value}'",
                        )
                    else:
                        # Task not found - return -32001 per spec
                        return self._error_response(
                            request_id, -32001, f"Task not found: {message.task_id}"
                        )
            else:
                # Create new task from message
                task = self.task_store.create_task_from_message(message)

            # Check for custom responses and special commands in the first text part
            first_text = self._extract_first_text([message])

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
                            request_id, self._serialize_task(updated_task)
                        )
                    else:
                        # Complete with custom response
                        artifact = new_text_artifact("agent-output", custom_response)
                        self.task_store.add_task_artifact(task.id, artifact)

                        # Log task completion before completing task
                        log_task_completion(first_text)

                        self.task_store.complete_task(task.id)
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                # Check for special commands
                elif self.special_commands.is_special_command(first_text):
                    # For HANG commands, return task in working state without completing
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(task.id)
                        # Return task in working state, don't complete it
                        updated_task = self.task_store.get_task(task.id)
                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
                        )
                    elif first_text.strip().upper() == "FAIL_NODE":
                        # For FAIL_NODE, immediately fail task with error message (like stdio mode)
                        # Stdio mode returns: state="failed" with message "Mock agent failure: FAIL_NODE"
                        # A2A mode should be consistent: mark task failed and add error in status.message
                        self.task_store.fail_task(task.id)

                        # Create failure message for status.message field
                        # A2A protocol uses role="agent" not "assistant"
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

                        # Get task and set status.message per A2A spec §6.2
                        updated_task = self.task_store.get_task(task.id)
                        updated_task.status.message = failure_message

                        return self._success_response(
                            request_id, self._serialize_task(updated_task)
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
                            request_id, self._serialize_task(updated_task)
                        )
                else:
                    # Regular messages: for mock agent, complete with default response
                    # Note: Per §7.1, real agents would stay in working/submitted state,
                    # but mock agent completes immediately to support testing workflows
                    self.task_store.set_task_working(task.id)
                    default_response = f"Mock agent received: {first_text}"
                    artifact = new_text_artifact("agent-output", default_response)
                    self.task_store.add_task_artifact(task.id, artifact)
                    log_task_completion(first_text)
                    self.task_store.complete_task(task.id)
            else:
                # No text content: complete with generic response (testing support)
                self.task_store.set_task_working(task.id)
                artifact = new_text_artifact(
                    "agent-output", "Mock agent processed request"
                )
                self.task_store.add_task_artifact(task.id, artifact)
                self.task_store.complete_task(task.id)

            # Get task and serialize, honoring historyLength per §7.1.1
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

                # Log task completion before completing task
                log_task_completion(command_text)

                self.task_store.complete_task(task_id)
        except Exception:
            # Mark task as failed if command processing fails
            self.task_store.fail_task(task_id)

    def _handle_tasks_get(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A tasks/get request per §7.3."""
        # Validate params is a dict per §8 - return -32602 for invalid params type
        if not isinstance(params, dict):
            return self._error_response(
                request_id, -32602, "Invalid params: params must be an object"
            )

        # Per §7.4.1 TaskIdParams, the field is 'id' not 'taskId'
        task_id = params.get("id") or params.get("taskId")  # Support legacy alias
        if not task_id:
            return self._error_response(
                request_id, -32602, "Invalid params: 'id' field required"
            )

        task = self.task_store.get_task(task_id)
        if not task:
            return self._error_response(request_id, -32001, "Task not found")

        # Per §7.3.1 TaskQueryParams, honor optional historyLength parameter
        history_length = params.get("historyLength")
        return self._success_response(
            request_id, self._serialize_task(task, history_length)
        )

    def _handle_tasks_cancel(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A tasks/cancel request per §7.4."""
        # Validate params is a dict per §8 - return -32602 for invalid params type
        if not isinstance(params, dict):
            return self._error_response(
                request_id, -32602, "Invalid params: params must be an object"
            )

        # Per §7.4.1 TaskIdParams, the field is 'id' not 'taskId'
        task_id = params.get("id") or params.get("taskId")  # Support legacy alias
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
