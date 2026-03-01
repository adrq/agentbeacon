#!/usr/bin/env python3
"""Seed the agents table with available agents.

Usage:
    uv run python scripts/seed_agents.py [--db-path scheduler-9456.db]
"""

import argparse
import json
import sqlite3
import uuid


AGENTS = [
    {
        "name": "Demo Agent",
        "agent_type": "acp",
        "description": "Mock ACP agent for e2e testing",
        "config": {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "agentbeacon.mock_agent",
                "--mode",
                "acp",
                "--scenario",
                "demo",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "Showcase Agent",
        "agent_type": "acp",
        "description": "Non-interactive demo of all event types",
        "config": {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "agentbeacon.mock_agent",
                "--mode",
                "acp",
                "--scenario",
                "showcase",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "TC Lead Agent",
        "agent_type": "acp",
        "description": "Lead agent that delegates (for turn-complete E2E tests)",
        "config": {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "agentbeacon.mock_agent",
                "--mode",
                "acp",
                "--scenario",
                "delegate",
                "--delegate-to",
                "TC Child Agent",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "TC Child Agent",
        "agent_type": "acp",
        "description": "Child agent that ends turn (for turn-complete E2E tests)",
        "config": {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "agentbeacon.mock_agent",
                "--mode",
                "acp",
                "--scenario",
                "end-turn",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "Claude Code",
        "agent_type": "claude_sdk",
        "description": "Claude Code agent via Claude Agent SDK",
        "config": {
            "model": "claude-haiku-4-5-20251001",
            "max_turns": 50,
        },
    },
    {
        "name": "GitHub Copilot",
        "agent_type": "copilot_sdk",
        "description": "GitHub Copilot agent via Copilot SDK",
        "config": {
            "model": "gpt-5-mini",
        },
    },
]


def ensure_driver(conn, platform):
    """Return driver_id for platform, creating driver if needed."""
    row = conn.execute(
        "SELECT id FROM drivers WHERE platform = ?", (platform,)
    ).fetchone()
    if row:
        return row[0]
    driver_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO drivers (id, name, platform, config) VALUES (?, ?, ?, '{}')",
        (driver_id, platform, platform),
    )
    print(f"  Created driver: {platform} ({driver_id})")
    return driver_id


def main():
    parser = argparse.ArgumentParser(description="Seed agents table")
    parser.add_argument(
        "--db-path", default="scheduler-9456.db", help="Path to SQLite database"
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    # Pre-create drivers for all platforms used by agents
    driver_cache = {}
    for agent in AGENTS:
        platform = agent["agent_type"]
        if platform not in driver_cache:
            driver_cache[platform] = ensure_driver(conn, platform)

    for agent in AGENTS:
        existing = conn.execute(
            "SELECT id FROM agents WHERE name = ?", (agent["name"],)
        ).fetchone()
        if existing:
            print(f"  Already exists: {agent['name']} ({existing[0]})")
            continue
        agent_id = str(uuid.uuid4())
        config_json = json.dumps(agent.get("config", {}))
        driver_id = driver_cache[agent["agent_type"]]
        conn.execute(
            "INSERT INTO agents (id, name, description, agent_type, driver_id, config, enabled)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                agent_id,
                agent["name"],
                agent["description"],
                agent["agent_type"],
                driver_id,
                config_json,
            ),
        )
        print(f"  Created: {agent['name']} ({agent_id})")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
