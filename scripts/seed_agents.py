#!/usr/bin/env python3
"""Seed the agents table with available agents.

Usage:
    uv run python scripts/seed_agents.py [--db-path scheduler-9456.db]
    uv run python scripts/seed_agents.py --db-url postgres://user:pass@localhost/dbname
"""

import argparse
import json
import sqlite3
import sys
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
        "name": "TC Markdown Lead Agent",
        "agent_type": "acp",
        "description": "Lead agent that delegates to TC Child Markdown Agent (for markdown rendering E2E tests)",
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
                "TC Child Markdown Agent",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "TC Child Markdown Agent",
        "agent_type": "acp",
        "description": "Child agent that responds with rich markdown (for markdown rendering E2E tests)",
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
                "end-turn-markdown",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "TC Msg Lead Agent",
        "agent_type": "acp",
        "description": "Lead agent for messaging E2E tests (delegates to TC Msg Child Agent)",
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
                "TC Msg Child Agent",
            ],
            "timeout": 60,
        },
    },
    {
        "name": "TC Msg Child Agent",
        "agent_type": "acp",
        "description": "Child agent that sends message to parent via REST API then ends turn",
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
                "end-turn-message",
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


class _PgConnWrapper:
    """Wraps psycopg2 connection to match sqlite3's conn.execute() API."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_driver_id(conn, platform):
    """Return driver_id for platform. Raises if driver not found."""
    row = conn.execute(
        "SELECT id FROM drivers WHERE platform = ?", (platform,)
    ).fetchone()
    if row:
        return row[0]
    raise RuntimeError(
        f"Driver not found for platform '{platform}'. "
        "Drivers are created by migration 0005 — run migrations first."
    )


def open_connection(db_path=None, db_url=None):
    """Open a database connection with unified sqlite3-style API."""
    if db_url and db_url.startswith("postgres"):
        import psycopg2

        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        return _PgConnWrapper(conn)
    path = db_path or "scheduler-9456.db"
    return sqlite3.connect(path)


def main():
    parser = argparse.ArgumentParser(description="Seed agents table")
    parser.add_argument("--db-path", default=None, help="Path to SQLite database")
    parser.add_argument("--db-url", default=None, help="Database URL (postgres://...)")
    args = parser.parse_args()

    if args.db_url and args.db_path:
        print("Error: specify --db-url or --db-path, not both", file=sys.stderr)
        sys.exit(1)

    conn = open_connection(db_path=args.db_path, db_url=args.db_url)

    # Look up drivers for all platforms used by agents
    driver_cache = {}
    for agent in AGENTS:
        platform = agent["agent_type"]
        if platform not in driver_cache:
            driver_cache[platform] = get_driver_id(conn, platform)

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
