---
title: MCP Servers
description: Giving agents tools beyond coding with MCP server configuration.
---

## What MCP Servers Do

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers give agents tools beyond their built-in coding capabilities — browser automation, database access, API calls, file system operations, and more.

When you attach an MCP server to an agent config, every session of that agent gets access to those tools.

## Adding Servers to an Agent

MCP servers are configured per-agent in the `mcpServers` field:

```json
{
  "name": "frontend-worker",
  "driver": "claude_sdk",
  "mcpServers": {
    "server-name": {
      "command": "command-to-run",
      "args": ["arg1", "arg2"],
      "env": {
        "KEY": "value"
      }
    }
  }
}
```

Each server entry specifies:
- **command** — The executable to run
- **args** — Command-line arguments
- **env** (optional) — Environment variables passed to the server process

## Example: Playwright for Browser Automation

```json
{
  "name": "e2e-tester",
  "driver": "claude_sdk",
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

This gives the agent tools to navigate pages, click elements, fill forms, take screenshots, and verify UI behavior.

## Example: Custom API Server

```json
{
  "mcpServers": {
    "internal-api": {
      "command": "node",
      "args": ["./tools/api-server.js"],
      "env": {
        "API_BASE_URL": "http://localhost:3000"
      }
    }
  }
}
```
