# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Configuration loading for mock agent."""

import json
import sys
from typing import Dict, Optional


def load_responses(config_file: Optional[str]) -> Dict[str, str]:
    if not config_file:
        return {}

    try:
        with open(config_file, "r") as f:
            responses = json.load(f)

        if not isinstance(responses, dict):
            print(
                f"Warning: Config file must contain a JSON object, got {type(responses).__name__}",
                file=sys.stderr,
            )
            return {}

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
