---
title: Storage
description: Database configuration for SQLite and PostgreSQL.
---

## Default: SQLite

Out of the box, AgentBeacon uses a SQLite database in its data directory:

```
~/.local/share/agentbeacon/db/scheduler-{port}.db    # Linux, e.g. scheduler-9456.db
```

On other platforms the data directory is the platform's application data directory (for example `~/Library/Application Support/agentbeacon` on macOS). The wiki search index sits beside the database in `db/wiki-index-{port}/`; it is rebuilt from the database on every start and can be deleted.

Set `AGENTBEACON_DATA_DIR` to use a different data directory; the database and index move with it. The database file is created readable only by your user.

Binaries built from source (for example with `make all` or `cargo build --release`) keep `scheduler-{port}.db` and `wiki-index-{port}/` in the current directory instead.

SQLite is for local and development use; PostgreSQL is recommended for production.

To use a specific SQLite file:

```bash
DATABASE_URL=sqlite:///home/you/agentbeacon.db agentbeacon
```

SQLite URLs take the forms `sqlite:<path>`, `sqlite://<path>` or `sqlite:///<absolute path>`, optionally followed by exactly one of `?mode=rwc`, `?mode=rw` or `?mode=ro`. Other SQLite URL options are not accepted.

## Recommended: PostgreSQL

PostgreSQL gives you proper concurrent access and standard backup tooling.

Set the `DATABASE_URL` environment variable:

```bash
DATABASE_URL=postgres://user:password@localhost:5432/agentbeacon agentbeacon
```

Or use the `--db-url` flag:

```bash
agentbeacon --db-url postgres://user:password@localhost:5432/agentbeacon
```

Pass the same `--db-url` or `DATABASE_URL` to `agentbeacon --setup-user` and to every start; the user lives in that database.

AgentBeacon runs migrations automatically on startup — just point it at an empty database and it handles the rest.

## Services and containers

When AgentBeacon runs under a service manager or in a container, set `AGENTBEACON_DATA_DIR` or `--db-url` so the database location doesn't depend on the service's environment.
