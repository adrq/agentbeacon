---
title: Introduction
description: What AgentBeacon is and the core concepts behind it.
---

## What is AgentBeacon

AgentBeacon is a **decision operations center** for AI coding agents. It coordinates teams of agents working on your codebase and surfaces only the decisions that need a human.

It is not a terminal multiplexer or a chat wrapper. You assign a feature to a lead agent, it delegates to implementers and reviewers, manages iteration loops, and presents you with a queue of structured questions. The interaction model is closer to triaging an inbox than monitoring a terminal.

## Core Concepts

**Execution** — A unit of work. You create an execution with a prompt ("Add rate limiting to /api/users"), assign it to an agent, and it runs to completion or until you cancel it.

**Session** — A running instance of an agent within an execution. The lead agent's session spawns child sessions when it delegates. Sessions form a tree.

**Agent** — A configured specialist type. Defines the model, system prompt, role, and tools an agent session uses. You create agent configs once and reuse them across executions.

**Driver** — The underlying agentic harness that powers an agent session. AgentBeacon orchestrates coding agents you already have installed (Claude Code, Copilot CLI, Codex CLI). A driver is the bridge between AgentBeacon and a specific CLI.

## How It Works

1. You assign a task to a lead agent
2. The lead breaks the work down and delegates to its team
3. Agents iterate: implement, review, revise
4. When the team hits genuine ambiguity, a structured decision surfaces to your queue
5. You answer, the team continues
6. Review the converged output with full decision and change logs

One engineer, multiple features in flight, decisions as the main touchpoint.
