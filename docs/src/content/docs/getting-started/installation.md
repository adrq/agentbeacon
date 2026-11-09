---
title: Installation
description: How to install AgentBeacon on your system.
---

## Platform

Linux (x86_64 and aarch64). macOS support coming soon.

## Install

Pick your preferred package manager:

```bash
# Python (pick one)
uv tool install agentbeacon        # recommended
pipx install agentbeacon
pip install --user agentbeacon

# npm
npm install -g agentbeacon
```

Then install agent SDK dependencies, create your user, and start:

```bash
agentbeacon --setup          # install Claude + Copilot SDK dependencies
agentbeacon --setup-user     # create your user
agentbeacon                  # start AgentBeacon
# Open http://localhost:9456 and sign in
```

`--setup-user` shows where the user is stored and asks for a username and password when it creates the user. For scripted installs, set `AGENTBEACON_USERNAME` and `AGENTBEACON_PASSWORD` instead. Run `agentbeacon --setup` again after upgrading to update the SDKs; it doesn't touch the user. See [Authentication](/docs/configuration/authentication/).

:::tip[Set up PostgreSQL]
AgentBeacon defaults to a SQLite database in its data directory, which is fine for local use. For production, set up PostgreSQL — see [Storage](/docs/configuration/storage/).
:::

## Prerequisites

- **Node.js 20+** — required for agent SDK dependencies
- **At least one coding agent CLI installed:**
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
  - [Copilot CLI](https://github.com/github/copilot-cli)
  - [Codex CLI](https://github.com/openai/codex)
  - [OpenCode](https://github.com/anomalyco/opencode) (coming soon)
  - Any [ACP](https://agentclientprotocol.com/get-started/introduction)-compatible agent
- **An API key or subscription** for the corresponding provider

AgentBeacon orchestrates coding agents you already have installed — it doesn't bundle or replace them.

## Build from Source

:::note
Only needed if you want to develop or contribute. Most users should use the package install above.
:::

```bash
git clone https://github.com/adrq/agentbeacon.git
cd agentbeacon
make all
make run
# Open http://localhost:9456
```

Requires Rust toolchain and Node.js 20+.

## Port Configuration

Default port is `9456`. Override with:

```bash
AGENTBEACON_PORT=9457 agentbeacon
```

Multiple instances can run simultaneously on different ports without conflicts.

## Storage

AgentBeacon uses SQLite by default — no setup needed. For production deployments, PostgreSQL is recommended. See [Storage](/docs/configuration/storage/) for details.
