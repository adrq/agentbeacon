"""Stdio mode handler for line-oriented text input/output."""

import json
import sys
import uuid
from datetime import datetime
from typing import Any, Dict

from .special_commands import SpecialCommands
from .file_logger import log_task_completion


class StdioHandler:
    """Handler for stdio mode communication."""

    def __init__(self, custom_responses: Dict[str, str] = None):
        self.special_commands = SpecialCommands()
        self.custom_responses = custom_responses or {}

    def run(self):
        """Main stdio processing loop."""
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                response = self.process_input(line)
                print(json.dumps(response), flush=True)

        except (EOFError, KeyboardInterrupt):
            pass

    def process_input(self, input_text: str) -> Dict[str, Any]:
        """Process input and return A2A-compliant response."""
        try:
            json_input = json.loads(input_text)
            if isinstance(json_input, dict) and "request" in json_input:
                request_data = json_input["request"]
                if "prompt" in request_data:
                    text_content = request_data["prompt"]
                elif "task" in request_data:
                    task = request_data["task"]
                    if isinstance(task, dict):
                        if "message" in task:
                            message = task["message"]
                            if isinstance(message, dict) and "parts" in message:
                                parts = message["parts"]
                                if parts and len(parts) > 0:
                                    first_part = parts[0]
                                    if first_part.get("kind") == "text":
                                        text_content = first_part.get("text", "")
                                    else:
                                        text_content = str(task)
                                else:
                                    text_content = str(task)
                            else:
                                text_content = str(task)
                        else:
                            text_content = str(task)
                    else:
                        text_content = str(task)
                else:
                    text_content = str(request_data)
            else:
                text_content = str(json_input)
        except json.JSONDecodeError:
            text_content = input_text

        if text_content in self.custom_responses:
            custom_response = self.custom_responses[text_content]
            if custom_response == "HANG":
                import time

                time.sleep(3600)
                response_text = "This should never be reached"
            else:
                response_text = custom_response
        elif self.special_commands.is_special_command(text_content):
            result = self.special_commands.handle_command(text_content, stdio_mode=True)
            if result == "STDIO_FAILURE":
                return {
                    "taskStatus": {
                        "state": "failed",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "message": {
                            "kind": "message",
                            "messageId": str(uuid.uuid4()),
                            "role": "assistant",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": f"Mock agent failure: {text_content}",
                                }
                            ],
                        },
                    }
                }

            response_text = f"Mock response: {text_content}"
        else:
            response_text = f"Mock response: {text_content}"

        response = {
            "taskStatus": {
                "state": "completed",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            "artifacts": [
                {
                    "artifactId": str(uuid.uuid4()),
                    "name": "agent-output",
                    "description": "Output from mock agent execution",
                    "parts": [{"kind": "text", "text": response_text}],
                }
            ],
        }

        log_task_completion(input_text)

        return response


def process_input(
    input_text: str, custom_responses: Dict[str, str] = None
) -> Dict[str, Any]:
    """Process single input and return response (for testing)."""
    handler = StdioHandler(custom_responses)
    return handler.process_input(input_text)


def start_stdio_mode(custom_responses: Dict[str, str] = None):
    """Start stdio mode handler."""
    handler = StdioHandler(custom_responses)
    handler.run()
