"""Test that agent card returns correct URL for different ports.

This test validates that the scheduler's agent card endpoint returns
the correct RPC URL based on the port the scheduler is running on,
rather than a hardcoded localhost:9456 URL.
"""

import requests
import pytest

from tests.testhelpers import PortManager, scheduler_context


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_card_url_matches_scheduler_port(test_database):
    """Test that agent card URL uses the actual scheduler port, not hardcoded 9456."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]
        port = scheduler["port"]

        # Get agent card
        response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json", timeout=5
        )
        assert response.status_code == 200, f"Failed to get agent card: {response.text}"

        agent_card = response.json()

        # Verify the URL field uses the correct port
        expected_url = f"http://localhost:{port}/rpc"
        assert agent_card["url"] == expected_url, (
            f"Agent card URL should be {expected_url}, got {agent_card['url']}"
        )

        # Verify additional interfaces also use correct port
        assert "additionalInterfaces" in agent_card
        assert len(agent_card["additionalInterfaces"]) > 0

        additional_interface = agent_card["additionalInterfaces"][0]
        assert additional_interface["url"] == expected_url, (
            f"Additional interface URL should be {expected_url}, "
            f"got {additional_interface['url']}"
        )
        assert additional_interface["transport"] == "JSONRPC"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_agent_card_url_different_ports(test_database):
    """Test that agent card URL correctly reflects different scheduler ports."""
    port_manager = PortManager()

    # Hold the first port allocation while acquiring the second to guarantee uniqueness
    with port_manager.port_context() as port1:
        with scheduler_context(port=port1, db_url=test_database) as scheduler1:
            response1 = requests.get(
                f"{scheduler1['url']}/.well-known/agent-card.json", timeout=5
            )
            assert response1.status_code == 200
            card1 = response1.json()
            assert card1["url"] == f"http://localhost:{port1}/rpc"

            with port_manager.port_context() as port2:
                # Ensure we got a different port before starting the second scheduler
                assert port2 != port1, (
                    "Should get different ports for different schedulers"
                )

                with scheduler_context(port=port2, db_url=test_database) as scheduler2:
                    response2 = requests.get(
                        f"{scheduler2['url']}/.well-known/agent-card.json", timeout=5
                    )
                    assert response2.status_code == 200
                    card2 = response2.json()
                    assert card2["url"] == f"http://localhost:{port2}/rpc"

                    # Verify the URLs are different
                    assert card1["url"] != card2["url"], (
                        "Different scheduler instances should report different URLs"
                    )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_agent_card_respects_forwarded_headers(test_database):
    """Test that agent card URL uses X-Forwarded-Host and X-Forwarded-Proto headers."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Send request with X-Forwarded headers
        response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json",
            headers={
                "X-Forwarded-Host": "api.example.com",
                "X-Forwarded-Proto": "https",
            },
            timeout=5,
        )
        assert response.status_code == 200
        agent_card = response.json()

        # Verify the URL uses the forwarded headers
        assert agent_card["url"] == "https://api.example.com/rpc", (
            f"Agent card should use forwarded headers, got {agent_card['url']}"
        )
        assert (
            agent_card["additionalInterfaces"][0]["url"]
            == "https://api.example.com/rpc"
        )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_agent_card_public_url_override(test_database):
    """Test that PUBLIC_URL env variable overrides forwarded headers."""
    # Set PUBLIC_URL environment variable
    public_url = "https://production.example.com:8443"

    with scheduler_context(
        db_url=test_database, env={"PUBLIC_URL": public_url}
    ) as scheduler:
        scheduler_url = scheduler["url"]

        # Send request WITH forwarded headers - PUBLIC_URL should take priority
        response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json",
            headers={
                "X-Forwarded-Host": "other.example.com",
                "X-Forwarded-Proto": "https",
            },
            timeout=5,
        )
        assert response.status_code == 200
        agent_card = response.json()

        # PUBLIC_URL should override forwarded headers
        expected_url = f"{public_url}/rpc"
        assert agent_card["url"] == expected_url, (
            f"PUBLIC_URL should override forwarded headers, got {agent_card['url']}"
        )
        assert agent_card["additionalInterfaces"][0]["url"] == expected_url


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_agent_card_multi_proxy_forwarded_headers(test_database):
    """Test that multi-valued forwarded headers are parsed correctly (first value wins)."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Send request with comma-separated forwarded headers (multi-proxy scenario)
        response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json",
            headers={
                "X-Forwarded-Host": "client.example.com, proxy1.internal, proxy2.internal",
                "X-Forwarded-Proto": "https, http, http",
            },
            timeout=5,
        )
        assert response.status_code == 200
        agent_card = response.json()

        # Should use the first (leftmost) value from each header
        assert agent_card["url"] == "https://client.example.com/rpc", (
            f"Agent card should use first forwarded value, got {agent_card['url']}"
        )
        assert (
            agent_card["additionalInterfaces"][0]["url"]
            == "https://client.example.com/rpc"
        )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_agent_card_forwarded_headers_with_whitespace(test_database):
    """Test that forwarded headers with whitespace after commas are trimmed correctly."""
    with scheduler_context(db_url=test_database) as scheduler:
        scheduler_url = scheduler["url"]

        # Headers with whitespace after commas (realistic proxy scenario)
        response = requests.get(
            f"{scheduler_url}/.well-known/agent-card.json",
            headers={
                "X-Forwarded-Host": "api.example.com, proxy.internal",
                "X-Forwarded-Proto": "HTTPS, http",
            },
            timeout=5,
        )
        assert response.status_code == 200
        agent_card = response.json()

        # Should trim whitespace and normalize protocol to lowercase
        assert agent_card["url"] == "https://api.example.com/rpc", (
            f"Agent card should trim whitespace and lowercase proto, got {agent_card['url']}"
        )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_agent_card_priority_order(test_database):
    """Test the priority order: PUBLIC_URL > X-Forwarded > localhost."""
    port_manager = PortManager()

    with port_manager.port_context() as port:
        # Test 1: No env, no headers -> localhost
        with scheduler_context(port=port, db_url=test_database) as scheduler:
            response = requests.get(
                f"{scheduler['url']}/.well-known/agent-card.json", timeout=5
            )
            card = response.json()
            assert card["url"] == f"http://localhost:{port}/rpc"

        # Test 2: No env, with headers -> use forwarded headers
        with scheduler_context(port=port, db_url=test_database) as scheduler:
            response = requests.get(
                f"{scheduler['url']}/.well-known/agent-card.json",
                headers={
                    "X-Forwarded-Host": "forwarded.example.com",
                    "X-Forwarded-Proto": "https",
                },
                timeout=5,
            )
            card = response.json()
            assert card["url"] == "https://forwarded.example.com/rpc"

        # Test 3: With env and headers -> PUBLIC_URL wins
        with scheduler_context(
            port=port,
            db_url=test_database,
            env={"PUBLIC_URL": "https://public.example.com"},
        ) as scheduler:
            response = requests.get(
                f"{scheduler['url']}/.well-known/agent-card.json",
                headers={
                    "X-Forwarded-Host": "forwarded.example.com",
                    "X-Forwarded-Proto": "http",
                },
                timeout=5,
            )
            card = response.json()
            assert card["url"] == "https://public.example.com/rpc"
