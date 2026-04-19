---
title: Drivers
description: Supported agent drivers and how to configure them.
---

## Overview

A driver is the bridge between AgentBeacon and a specific coding agent CLI. AgentBeacon orchestrates agents you already have installed — each driver knows how to start, communicate with, and manage a particular CLI.

## claude_sdk

Uses your installed [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI.

**Requirements:**
- Claude Code installed and authenticated (`claude` command available)
- Active Anthropic API key or Claude Pro/Team subscription

```json
{
  "name": "claude-lead",
  "driver": "claude_sdk",
  "model": "sonnet"
}
```

Models available: `opus`, `sonnet`, `haiku` (short aliases), or full model IDs like `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`.

## copilot_sdk

Uses [GitHub Copilot CLI](https://github.com/github/copilot-cli).

**Requirements:**
- Copilot CLI installed
- `GITHUB_TOKEN` environment variable set with a valid token that has Copilot access

```json
{
  "name": "copilot-worker",
  "driver": "copilot_sdk"
}
```

Set `GITHUB_TOKEN` before starting AgentBeacon:

```bash
export GITHUB_TOKEN=ghp_your_token_here
agentbeacon
```

## codex_sdk

Uses [OpenAI Codex CLI](https://github.com/openai/codex).

**Requirements:**
- Codex CLI installed and authenticated (`codex` command available)

```json
{
  "name": "codex-worker",
  "driver": "codex_sdk"
}
```
