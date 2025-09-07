"""ACP mode handler for JSON-RPC over stdio communication."""

import json
import sys
from typing import Any, Dict

from .task_store import TaskStore
from .jsonrpc import JSONRPCDispatcher
from .file_logger import log_task_completion


class ACPHandler:
    """Handler for ACP (Agent Client Protocol) mode communication."""

    def __init__(self, custom_responses: Dict[str, str] = None):
        self.task_store = TaskStore()
        self.jsonrpc_dispatcher = JSONRPCDispatcher(self.task_store, custom_responses)
        self.custom_responses = custom_responses or {}

    def run(self):
        """Main ACP processing loop."""
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                    response = self.jsonrpc_dispatcher.handle_request(request)
                    print(json.dumps(response), flush=True)

                    # Send notifications for session/prompt
                    if (
                        request.get("method") == "session/prompt"
                        and "error" not in response
                    ):
                        self._send_notification(request)

                except json.JSONDecodeError:
                    # Invalid JSON - send parse error
                    error_response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                    print(json.dumps(error_response), flush=True)

        except (EOFError, KeyboardInterrupt):
            pass

    def _send_notification(self, original_request: Dict[str, Any]):
        """Send session update notification for ACP session/prompt."""
        params = original_request.get("params", {})
        session_id = params.get("sessionId")

        if session_id:
            # Extract prompt text for response
            prompt_parts = params.get("prompt", [])
            prompt_text = ""
            for part in prompt_parts:
                if part.get("type") == "text":
                    prompt_text += part.get("text", "")

            # Check for custom response or use default
            if prompt_text in self.custom_responses:
                response_text = self.custom_responses[prompt_text]
            else:
                response_text = f"Mock ACP response: {prompt_text}"

            # Log task completion before sending response
            if prompt_text:
                log_task_completion(prompt_text)

            # Send notification with response
            notification = {
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
    """Start ACP mode handler."""
    handler = ACPHandler(custom_responses)
    handler.run()
