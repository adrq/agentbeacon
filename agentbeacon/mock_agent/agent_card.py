"""Agent card factory for mock agent identification (A2A v1.0 format).

The v1.0 agent card uses ``supportedInterfaces`` instead of top-level
``url`` / ``protocolVersion`` / ``preferredTransport``.  Because the
a2a-sdk's ``AgentCard`` pydantic model still reflects v0.3.0, we build
the card as a plain dict.
"""


def create_agent_card_dict(base_url: str, port: int = 8080) -> dict:
    """Create A2A v1.0 agent card as a dictionary for JSON serialization."""
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
