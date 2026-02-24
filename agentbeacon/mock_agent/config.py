"""Configuration loading for mock agent."""

import json
import sys
from typing import Dict, Optional


def load_responses(config_file: Optional[str]) -> Dict[str, str]:
    """Load custom responses from JSON config file.

    Args:
        config_file: Path to JSON file containing prompt->response mappings

    Returns:
        Dictionary of custom responses, empty if file doesn't exist or is invalid
    """
    if not config_file:
        return {}

    try:
        with open(config_file, "r") as f:
            responses = json.load(f)

        # Validate that responses is a dict with string keys/values
        if not isinstance(responses, dict):
            print(
                f"Warning: Config file must contain a JSON object, got {type(responses).__name__}",
                file=sys.stderr,
            )
            return {}

        # Convert all keys/values to strings
        str_responses = {}
        for key, value in responses.items():
            str_responses[str(key)] = str(value)

        return str_responses

    except FileNotFoundError:
        print(f"Warning: Config file not found: {config_file}", file=sys.stderr)
        return {}
    except json.JSONDecodeError as e:
        print(
            f"Warning: Invalid JSON in config file {config_file}: {e}", file=sys.stderr
        )
        return {}
    except Exception as e:
        print(f"Warning: Error reading config file {config_file}: {e}", file=sys.stderr)
        return {}
