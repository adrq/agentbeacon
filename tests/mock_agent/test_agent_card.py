"""Integration tests for agent card endpoint.

These tests validate the agent card factory and HTTP serving functionality:
- Agent card structure and content validation
- Skills definition and examples
- Capabilities reporting accuracy
- URL generation and transport configuration
- Cross-mode consistency

"""

import httpx


def test_agent_card_a2a_protocol_compliance(mock_agent_a2a):
    """Test agent card compliance with A2A v1.0 specification."""
    response = httpx.get(f"{mock_agent_a2a}/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/json"
    card = response.json()

    # Core A2A v1.0 required fields
    assert card["name"] == "Mock A2A Agent"
    assert card["version"] == "1.0.0"

    # v1.0: URL moved to supportedInterfaces
    assert "supportedInterfaces" in card
    interfaces = card["supportedInterfaces"]
    assert len(interfaces) >= 1
    assert interfaces[0]["url"] == f"{mock_agent_a2a}/rpc"
    assert interfaces[0]["protocolBinding"] == "JSONRPC"
    assert interfaces[0]["protocolVersion"] == "1.0"

    # Required structure fields
    assert "description" in card
    assert "capabilities" in card
    assert "defaultInputModes" in card
    assert "defaultOutputModes" in card
    assert "skills" in card

    # Mock agent capabilities
    capabilities = card["capabilities"]
    assert capabilities["streaming"] == False  # noqa
    assert capabilities["pushNotifications"] == False  # noqa

    # Protocol compliance
    assert "application/json" in card["defaultInputModes"]
    assert "application/json" in card["defaultOutputModes"]

    # Skills structure
    skills = card["skills"]
    assert isinstance(skills, list)
    assert len(skills) >= 1

    skill = skills[0]
    assert "id" in skill
    assert "name" in skill
    assert "description" in skill


# Port configuration test is handled by test_cli_modes.py
