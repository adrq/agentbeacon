"""Agent card factory using A2A SDK for mock agent identification."""

from a2a.types import AgentCard, AgentCapabilities


def create_agent_card(base_url: str, port: int = 8080) -> AgentCard:
    """Create A2A-compliant agent card for mock agent."""
    from a2a.types import AgentSkill

    return AgentCard(
        protocol_version="0.3.0",
        name="Mock A2A Agent",
        preferred_transport="JSONRPC",
        url=f"{base_url}/rpc",
        version="1.0.0",
        description="Mock agent for testing AgentBeacon workflows",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            AgentSkill(
                id="mock-testing",
                name="Mock Testing",
                description="Provides mock responses for testing AgentBeacon workflows",
                tags=["testing", "mock", "development"],
            )
        ],
    )


def create_agent_card_dict(base_url: str, port: int = 8080) -> dict:
    """Create agent card as dictionary for JSON serialization."""
    card = create_agent_card(base_url, port)
    return card.model_dump(exclude_none=True)
