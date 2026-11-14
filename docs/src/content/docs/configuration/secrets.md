---
title: Secrets
description: Giving agents the credentials their work needs.
---

## What Secrets Are

A secret is a named credential, such as an API token, that belongs to a project. Names follow environment-variable style: `[A-Z_][A-Z0-9_]*`, up to 255 bytes. Values are text up to 64 KiB.

A secret is available to an execution only after you attach it. Every session in that execution can then fetch it.

## Managing Secrets

- **Project page → Secrets**: add a secret, replace its value, or delete it. Values are never shown again after you save them.
- **New execution**: select the project's secrets to attach, or create one and attach it in one step.
- **Running execution → Secrets**: attach or detach secrets while the execution runs. A change is available to agents on their next fetch.

A secret that is attached to a running execution cannot be deleted; detach it first.

## How Agents Use Secrets

Agents see the names of the secrets attached to their execution and fetch a value by name using their own session credential:

```
GET /api/v1/executions/{execution_id}/secrets
GET /api/v1/secrets/{name}
```

The list returns `[{"name", "attached_at"}]`; a fetch returns `{"name": "NAME", "value": "…"}`.

Their briefing tells them to use values without printing them, to fetch again whenever they need one, and to ask you if they need a secret that isn't attached. A value you replace or a secret you detach takes effect on their next fetch.

## Limits

- Values are stored in the scheduler database without encryption.
- AgentBeacon does not scan or redact agent output. If a value is exposed, replace it with a new one.
