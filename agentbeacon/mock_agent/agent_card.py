# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later


def create_agent_card_dict(base_url: str, port: int = 8080) -> dict:
    return {
        "name": "Mock A2A Agent",
        "version": "1.0.0",
        "description": "Mock agent for testing AgentBeacon workflows",
        "supportedInterfaces": [
            {
                "url": f"{base_url}/rpc",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": "mock-testing",
                "name": "Mock Testing",
                "description": "Provides mock responses for testing AgentBeacon workflows",
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json", "text/plain"],
            }
        ],
    }
