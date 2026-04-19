---
title: Storage
description: Database configuration for SQLite and PostgreSQL.
---

## Default: SQLite

Out of the box, AgentBeacon uses SQLite with a database file in your **current working directory**:

```
scheduler-{port}.db    # e.g. scheduler-9456.db
```

This means you should always start AgentBeacon from the same directory, or your data won't be there. SQLite is convenient for trying things out but we recommend PostgreSQL for any serious use.

To use a fixed SQLite path regardless of where you run from:

```bash
DATABASE_URL=sqlite:///home/you/agentbeacon.db agentbeacon
```

## Recommended: PostgreSQL

PostgreSQL is recommended for anything beyond experimentation. It gives you proper concurrent access, standard backup tooling, and you don't need to worry about which directory you started from.

Set the `DATABASE_URL` environment variable:

```bash
DATABASE_URL=postgres://user:password@localhost:5432/agentbeacon agentbeacon
```

Or use the `--db-url` flag:

```bash
agentbeacon --db-url postgres://user:password@localhost:5432/agentbeacon
```

AgentBeacon runs migrations automatically on startup — just point it at an empty database and it handles the rest.
