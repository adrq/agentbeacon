"""Stdio mode handler for line-oriented text input/output."""

import json
import sys
import uuid
from datetime import datetime
from typing import Any, Dict

from .special_commands import SpecialCommands


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
        # Try to parse as JSON first
        try:
            json_input = json.loads(input_text)
            if isinstance(json_input, dict) and "request" in json_input:
                # Handle wrapped JSON request
                request_data = json_input["request"]
                if "prompt" in request_data:
                    text_content = request_data["prompt"]
                elif "task" in request_data:
                    text_content = request_data["task"]
                else:
                    text_content = str(request_data)
            else:
                text_content = str(json_input)
        except json.JSONDecodeError:
            # Handle as plain text
            text_content = input_text

        # Check for custom response first
        if text_content in self.custom_responses:
            custom_response = self.custom_responses[text_content]
            # Handle special HANG value in config
            if custom_response == "HANG":
                # This will hang for 1 hour like special commands
                import time

                time.sleep(3600)
                response_text = "This should never be reached"
            else:
                response_text = custom_response
        # Check for special commands
        elif self.special_commands.is_special_command(text_content):
            result = self.special_commands.handle_command(text_content, stdio_mode=True)

            # Handle stdio failure mode
            if result == "STDIO_FAILURE":
                return {
                    "taskStatus": {
                        "state": "failed",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"text": f"Mock agent failure: {text_content}"}
                            ],
                        },
                    }
                }

            # For stdio mode, still return Mock response format as tests expect
            response_text = f"Mock response: {text_content}"
        else:
            response_text = f"Mock response: {text_content}"

        # Return A2A-compliant response format
        return {
            "taskStatus": {
                "state": "completed",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
            "artifacts": [
                {
                    "artifactId": str(uuid.uuid4()),
                    "name": "agent-output",
                    "description": "Output from mock agent execution",
                    "parts": [{"text": response_text}],
                }
            ],
        }


def start_stdio_mode(custom_responses: Dict[str, str] = None):
    """Start stdio mode handler."""
    handler = StdioHandler(custom_responses)
    handler.run()
