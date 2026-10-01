# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx


def test_agent_card_a2a_protocol_compliance(mock_agent_a2a):
    response = httpx.get(f"{mock_agent_a2a}/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/json"
    card = response.json()

    assert card["name"] == "Mock A2A Agent"
    assert card["version"] == "1.0.0"

    assert "supportedInterfaces" in card
    interfaces = card["supportedInterfaces"]
    assert len(interfaces) >= 1
    assert interfaces[0]["url"] == f"{mock_agent_a2a}/rpc"
    assert interfaces[0]["protocolBinding"] == "JSONRPC"
    assert interfaces[0]["protocolVersion"] == "1.0"

    assert "description" in card
    assert "capabilities" in card
    assert "defaultInputModes" in card
    assert "defaultOutputModes" in card
    assert "skills" in card

    capabilities = card["capabilities"]
    assert capabilities["streaming"] == False  # noqa
    assert capabilities["pushNotifications"] == False  # noqa

    assert "application/json" in card["defaultInputModes"]
    assert "application/json" in card["defaultOutputModes"]

    skills = card["skills"]
    assert isinstance(skills, list)
    assert len(skills) >= 1

    skill = skills[0]
    assert "id" in skill
    assert "name" in skill
    assert "description" in skill
