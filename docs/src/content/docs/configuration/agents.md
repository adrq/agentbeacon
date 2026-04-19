---
title: Agents
description: Configuring agent types with models, instructions, and tools.
---

## Agent Config Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Display name for the agent |
| `driver` | yes | Which CLI to use (`claude_sdk`, `copilot_sdk`, `codex_sdk`) |
| `model` | no | Model override (driver default used if omitted) |
| `system_prompt` | no | Additional instructions appended to the agent's prompt |
| `mcpServers` | no | MCP tool servers available to this agent (see [MCP Servers](/docs/configuration/mcp-servers/)) |

## Roles and the Agent Pool

Roles are **not configured on the agent**. When you create an execution, you select a **root agent** and an **agent pool** — the set of agents available for delegation.

The system assigns roles based on where an agent ends up in the delegation tree:

- **root-lead** — the agent you selected as root. Receives the execution prompt, coordinates the team.
- **sub-lead** — an agent that was delegated to and can further delegate.
- **leaf** — an agent at the maximum delegation depth. Executes tasks directly.

The same agent config can play different roles in different executions depending on how the lead chooses to delegate.

## Example: Minimal Claude Agent

```json
{
  "name": "claude-worker",
  "driver": "claude_sdk"
}
```

## Example: Agent with Custom Instructions

```json
{
  "name": "security-reviewer",
  "driver": "claude_sdk",
  "model": "sonnet",
  "system_prompt": "You are a security-focused code reviewer. Flag OWASP top 10 vulnerabilities, injection risks, and authentication bypasses. Be specific about the risk and fix."
}
```

## Briefings

Agents receive a **briefing** when assigned work. Briefings include:

- Project-level context (conventions, architecture notes, constraints)
- The execution prompt and any parent context
- Wiki pages the project has marked as relevant

Briefings are composed automatically — you configure the project-level content in the project settings. Agents see the briefing as part of their system prompt when they start working.
