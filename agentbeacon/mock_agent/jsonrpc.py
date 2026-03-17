"""JSON-RPC request dispatcher for A2A and ACP protocols."""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from .task_store import TaskStore, new_text_artifact
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

    def _coerce_history_length(
        self, value: Any, field_path: str, request_id: Any
    ) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Validate and coerce a historyLength value (int or numeric string per A2A v1.0 schema).

        Returns (parsed_int, None) on success, or (None, error_response) on failure.
        """
        if value is None:
            return None, None
        if isinstance(value, bool):
            return None, self._error_response(
                request_id,
                -32602,
                f"Invalid params: '{field_path}' must be a non-negative integer",
            )
        if isinstance(value, str):
            if not value.lstrip("-").isdigit():
                return None, self._error_response(
                    request_id,
                    -32602,
                    f"Invalid params: '{field_path}' must be a non-negative integer",
                )
            value = int(value)
        if not isinstance(value, int) or value < 0:
            return None, self._error_response(
                request_id,
                -32602,
                f"Invalid params: '{field_path}' must be a non-negative integer",
            )
        return value, None

    def _parse_history_length(
        self, params: Dict[str, Any], request_id: Any
    ) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Extract and validate historyLength from configuration (SendMessage)."""
        config = params.get("configuration")
        if config is None:
            return None, None
        if not isinstance(config, dict):
            return None, self._error_response(
                request_id, -32602, "Invalid params: 'configuration' must be an object"
            )
        return self._coerce_history_length(
            config.get("historyLength"), "configuration.historyLength", request_id
        )

    def _serialize_task(
        self, task: Dict[str, Any], history_length: Optional[int] = None
    ) -> Dict[str, Any]:
        """Serialize task dict with optional history limiting."""
        result = dict(task)
        if history_length is not None and "history" in result:
            if history_length == 0:
                result["history"] = []
            elif len(result["history"]) > history_length:
                result["history"] = result["history"][-history_length:]
        return result

    def _validate_and_parse_message(
        self, msg_data: Any, request_id: Any
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Validate and parse message data, returning (message_dict, error_response).

        Returns (message_dict, None) on success, or (None, error_response) on failure.
        The returned message_dict is in v1.0 format (flat parts, ROLE_* roles).
        """
        try:
            if not isinstance(msg_data, dict):
                return None, self._error_response(
                    request_id, -32602, "Invalid params: 'message' must be an object"
                )

            if "role" not in msg_data:
                return None, self._error_response(
                    request_id, -32602, "Invalid params: 'message.role' is required"
                )
            if "parts" not in msg_data:
                return None, self._error_response(
                    request_id, -32602, "Invalid params: 'message.parts' is required"
                )

            role_str = msg_data["role"]
            if role_str not in ("ROLE_USER", "ROLE_AGENT"):
                return None, self._error_response(
                    request_id,
                    -32602,
                    f"Invalid params: message.role must be 'ROLE_USER' or 'ROLE_AGENT', got '{role_str}'",
                )

            parts_data = msg_data.get("parts", [])
            if not isinstance(parts_data, list) or len(parts_data) == 0:
                return None, self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: 'message.parts' must be a non-empty array",
                )

            # Validate each part has a recognized content field with correct type
            for i, part_data in enumerate(parts_data):
                if not isinstance(part_data, dict):
                    return None, self._error_response(
                        request_id,
                        -32602,
                        f"Invalid params: message.parts[{i}] must be an object",
                    )
                # Reject v0.3 kind-tagged parts — clean break
                if "kind" in part_data:
                    return None, self._error_response(
                        request_id,
                        -32602,
                        f"Invalid params: message.parts[{i}] contains legacy 'kind' field; use v1.0 flat format",
                    )
                if "text" in part_data:
                    if not isinstance(part_data["text"], str):
                        return None, self._error_response(
                            request_id,
                            -32602,
                            f"Invalid params: message.parts[{i}].text must be a string",
                        )
                elif "raw" in part_data:
                    if not isinstance(part_data["raw"], str):
                        return None, self._error_response(
                            request_id,
                            -32602,
                            f"Invalid params: message.parts[{i}].raw must be a string",
                        )
                elif "url" in part_data:
                    if not isinstance(part_data["url"], str):
                        return None, self._error_response(
                            request_id,
                            -32602,
                            f"Invalid params: message.parts[{i}].url must be a string",
                        )
                elif "data" in part_data:
                    pass  # data can be any JSON value
                else:
                    return None, self._error_response(
                        request_id,
                        -32602,
                        f"Invalid params: message.parts[{i}] must contain 'text', 'raw', 'url', or 'data'",
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

            context_id = msg_data.get("contextId", str(uuid.uuid4()))
            if not isinstance(context_id, str):
                return None, self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: 'message.contextId' must be a string",
                )

            if "taskId" in msg_data and not isinstance(msg_data["taskId"], str):
                return None, self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: 'message.taskId' must be a string",
                )

            # Build v1.0 message dict — pass through parts as-is (already validated)
            message = {
                "messageId": message_id,
                "role": role_str,
                "parts": parts_data,
                "contextId": context_id,
            }
            if "taskId" in msg_data:
                message["taskId"] = msg_data["taskId"]
            if "metadata" in msg_data:
                message["metadata"] = msg_data["metadata"]
            if "extensions" in msg_data:
                message["extensions"] = msg_data["extensions"]
            if "referenceTaskIds" in msg_data:
                message["referenceTaskIds"] = msg_data["referenceTaskIds"]

            return message, None

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

            if method == "SendMessage":
                return self._handle_message_send_sync(request_id, params)
            elif method == "GetTask":
                return self._handle_tasks_get(request_id, params)
            elif method == "CancelTask":
                return self._handle_tasks_cancel(request_id, params)
            elif method == "initialize":
                return self._handle_acp_initialize(request_id, params)
            elif method == "session/new":
                return self._handle_acp_session_new(request_id, params)
            elif method == "session/prompt":
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

            if method == "SendMessage":
                return await self._handle_message_send_async(request_id, params)
            elif method == "GetTask":
                return self._handle_tasks_get(request_id, params)
            elif method == "CancelTask":
                return self._handle_tasks_cancel(request_id, params)
            else:
                return self._error_response(request_id, -32601, "Method not found")

        except Exception as e:
            return self._error_response(
                request.get("id"), -32603, f"Internal error: {str(e)}"
            )

    def _is_valid_jsonrpc(self, request: Dict[str, Any]) -> bool:
        return (
            isinstance(request, dict)
            and request.get("jsonrpc") == "2.0"
            and "method" in request
            and "id" in request
        )

    def _handle_message_send_sync(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A SendMessage request."""
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

            history_length, config_error = self._parse_history_length(
                params, request_id
            )
            if config_error:
                return config_error

            task_id = message.get("taskId")
            if task_id:
                task = self.task_store.append_message_to_task(task_id, message)
                if not task:
                    existing_task = self.task_store.get_task(task_id)
                    if existing_task:
                        return self._error_response(
                            request_id,
                            -32004,
                            f"Task cannot be continued: task is in terminal state '{existing_task['status']['state']}'",
                        )
                    else:
                        return self._error_response(
                            request_id, -32001, f"Task not found: {task_id}"
                        )
            else:
                task = self.task_store.create_task_from_message(message)

            first_text = self._extract_first_text_from_message(message)
            tid = task["id"]

            if first_text:
                if first_text in self.custom_responses:
                    custom_response = self.custom_responses[first_text]
                    self.task_store.set_task_working(tid)

                    if custom_response == "HANG":
                        updated_task = self.task_store.get_task(tid)
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                    else:
                        artifact = new_text_artifact(custom_response)
                        self.task_store.add_task_artifact(tid, artifact)
                        log_task_completion(first_text)
                        self.task_store.complete_task(tid)
                elif self.special_commands.is_special_command(first_text):
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(tid)
                        updated_task = self.task_store.get_task(tid)
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                    else:
                        self.task_store.set_task_working(tid)
                        result = self.special_commands.handle_command(first_text)
                        if result:
                            artifact = new_text_artifact(result)
                            self.task_store.add_task_artifact(tid, artifact)
                            log_task_completion(first_text)
                            self.task_store.complete_task(tid)
                else:
                    self.task_store.set_task_working(tid)
                    default_response = f"Mock agent received: {first_text}"
                    artifact = new_text_artifact(default_response)
                    self.task_store.add_task_artifact(tid, artifact)
                    log_task_completion(first_text)
                    self.task_store.complete_task(tid)
            else:
                self.task_store.set_task_working(tid)
                artifact = new_text_artifact("Mock agent processed request")
                self.task_store.add_task_artifact(tid, artifact)
                self.task_store.complete_task(tid)

            updated_task = self.task_store.get_task(tid)
            serialized_task = self._serialize_task(updated_task, history_length)
            return self._success_response(request_id, {"task": serialized_task})

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _handle_message_send_async(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A SendMessage request asynchronously."""
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

            history_length, config_error = self._parse_history_length(
                params, request_id
            )
            if config_error:
                return config_error

            task_id = message.get("taskId")
            if task_id:
                task = self.task_store.append_message_to_task(task_id, message)
                if not task:
                    existing_task = self.task_store.get_task(task_id)
                    if existing_task:
                        return self._error_response(
                            request_id,
                            -32004,
                            f"Task cannot be continued: task is in terminal state '{existing_task['status']['state']}'",
                        )
                    else:
                        return self._error_response(
                            request_id, -32001, f"Task not found: {task_id}"
                        )
            else:
                task = self.task_store.create_task_from_message(message)

            first_text = self._extract_first_text_from_message(message)
            tid = task["id"]

            if first_text:
                if first_text in self.custom_responses:
                    custom_response = self.custom_responses[first_text]
                    self.task_store.set_task_working(tid)

                    if custom_response == "HANG":
                        updated_task = self.task_store.get_task(tid)
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                    else:
                        artifact = new_text_artifact(custom_response)
                        self.task_store.add_task_artifact(tid, artifact)
                        log_task_completion(first_text)
                        self.task_store.complete_task(tid)
                        updated_task = self.task_store.get_task(tid)
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                elif self.special_commands.is_special_command(first_text):
                    if first_text.strip().upper() == "HANG":
                        self.task_store.set_task_working(tid)
                        updated_task = self.task_store.get_task(tid)
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                    elif first_text.strip().upper() == "FAIL_NODE":
                        self.task_store.fail_task(tid)
                        updated_task = self.task_store.get_task(tid)
                        # Add failure message to status
                        updated_task["status"]["message"] = {
                            "messageId": f"{tid}-fail-response",
                            "role": "ROLE_AGENT",
                            "parts": [{"text": f"Mock agent failure: {first_text}"}],
                        }
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                    else:
                        self.task_store.set_task_working(tid)
                        asyncio.create_task(
                            self._process_special_command_async(tid, first_text)
                        )
                        updated_task = self.task_store.get_task(tid)
                        return self._success_response(
                            request_id, {"task": self._serialize_task(updated_task)}
                        )
                else:
                    self.task_store.set_task_working(tid)
                    default_response = f"Mock agent received: {first_text}"
                    artifact = new_text_artifact(default_response)
                    self.task_store.add_task_artifact(tid, artifact)
                    log_task_completion(first_text)
                    self.task_store.complete_task(tid)
            else:
                self.task_store.set_task_working(tid)
                artifact = new_text_artifact("Mock agent processed request")
                self.task_store.add_task_artifact(tid, artifact)
                self.task_store.complete_task(tid)

            updated_task = self.task_store.get_task(tid)
            serialized_task = self._serialize_task(updated_task, history_length)
            return self._success_response(request_id, {"task": serialized_task})

        except Exception as e:
            return self._error_response(request_id, -32603, f"Internal error: {str(e)}")

    async def _process_special_command_async(self, task_id: str, command_text: str):
        """Process special command asynchronously and update task when complete."""
        try:
            result = await self.special_commands.handle_command_async(command_text)
            if result:
                artifact = new_text_artifact(result)
                self.task_store.add_task_artifact(task_id, artifact)
                log_task_completion(command_text)
                self.task_store.complete_task(task_id)
        except Exception:
            self.task_store.fail_task(task_id)

    def _handle_tasks_get(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A GetTask request."""
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

        history_length, hl_error = self._coerce_history_length(
            params.get("historyLength"), "historyLength", request_id
        )
        if hl_error:
            return hl_error
        return self._success_response(
            request_id, self._serialize_task(task, history_length)
        )

    def _handle_tasks_cancel(
        self, request_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle A2A CancelTask request."""
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
            time.sleep(3600)

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

    def _extract_first_text_from_message(
        self, message: Dict[str, Any]
    ) -> Optional[str]:
        """Extract first text content from a message dict."""
        for part in message.get("parts", []):
            if "text" in part and isinstance(part["text"], str):
                return part["text"]
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
