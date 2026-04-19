---
title: Quick Start
description: Create your first execution in 5 minutes.
---

## Start the System

```bash
agentbeacon
```

Open [http://localhost:9456](http://localhost:9456) in your browser.

:::tip[Set up PostgreSQL]
AgentBeacon defaults to SQLite in your current directory, which is fine for trying things out. For anything beyond experimentation, set up PostgreSQL — see [Storage](/docs/configuration/storage/).
:::

## Create an Agent Config

1. Click **Agents** in the sidebar
2. Click **+ New Agent**
3. Fill in:
   - **Name**: something descriptive (e.g. "claude-implementer")
   - **Driver**: pick the driver matching your installed CLI (e.g. `claude_sdk`)
4. Save

Create at least two agents — one to coordinate and one to implement. When you create an execution, you'll pick a root agent and an agent pool.

## Create an Execution

1. Click **+ New** in the header
2. Write a prompt describing your task (e.g. "Add input validation to the signup form")
3. Select your lead agent
4. Choose a project (or create one)
5. Submit

The lead agent starts working. Watch the activity feed for progress.

## Answer Decisions

When the team hits an ambiguity, a decision appears in your queue:

1. Click the notification badge or check the decision queue
2. Read the structured question (options are pre-labeled)
3. Pick an option, write your own response, or let the agent decide
4. Submit — the team continues immediately

## Review the Result

Once the execution completes:
- Check the code changes in the diff panel
- Review the decision log to see what choices were made
- Inspect individual agent sessions if you need detail

That's it!
