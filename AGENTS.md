# AgentBeacon — Developer Guide

Quick orientation for AI coding agents working in this repository. (This file is symlinked as both `CLAUDE.md` and `AGENTS.md`.)

## Common Commands

```bash
# Build everything (Rust + frontend)
make all

# Run the system
make run

# Run all tests (Rust + Python integration)
make test-all

# Frontend dev mode with HMR
make dev-backend    # Terminal 1
make dev-frontend   # Terminal 2 → http://localhost:10456

# Run specific test suites
make test           # Rust tests only
uv run pytest -v    # Python integration tests

# Before committing changes
git add <files>
make pre-commit     # Run pre-commit hooks on staged files
```

## Multi-Instance Support

`AGENTBEACON_PORT` controls all port and DB derivation. Default is `9456`.

| Resource        | Formula                     | Default  |
|-----------------|-----------------------------|----------|
| Scheduler port  | `AGENTBEACON_PORT`          | 9456     |
| Vite dev port   | `AGENTBEACON_PORT + 1000`   | 10456    |
| SQLite DB file  | `scheduler-{port}.db`       | `scheduler-9456.db` |

```bash
# Run a second instance (no conflicts with the first):
AGENTBEACON_PORT=9457 make run
AGENTBEACON_PORT=9457 make dev-backend    # Terminal 1
AGENTBEACON_PORT=9457 make dev-frontend   # Terminal 2 → http://localhost:10457

# E2E test on non-default port:
AGENTBEACON_PORT=9457 ./scripts/manual_e2e.sh
```

The orchestrator binary also reads `AGENTBEACON_PORT`, so `AGENTBEACON_PORT=9457 ./bin/agentbeacon` works without `--scheduler-port`. The Vite dev port can be overridden independently via `VITE_DEV_PORT` env var if needed.

## Process Management — Be Careful

Multiple AgentBeacon instances may be running simultaneously on different ports. **Never blindly kill processes by name** (e.g., `pkill agentbeacon` or `killall agentbeacon-scheduler`). This will disrupt other running instances.

Safe patterns:
- Kill by port: `fuser -k 9456/tcp` (only kills the process on that specific port)
- Kill by PID: `kill <pid>` (after identifying the right process with `lsof -i :9456`)
- Kill process you started: track the PID from your own `make run` or `cargo run`

Unsafe patterns (NEVER do these):
- `pkill -f agentbeacon` — kills ALL instances across all ports
- `killall agentbeacon-scheduler` — same problem
- `kill -9` without confirming the PID belongs to your instance

## Project Structure

- **Rust crates**: `scheduler/`, `worker/`, `common/`
- **Orchestrator**: `orchestrator/` (spawns and monitors scheduler + workers)
- **Executors**: `executors/` (Node.js SDK wrappers for Claude, Copilot)
- **Frontend**: `web/` (Svelte 5 + TypeScript)
- **Docs & schemas**: `docs/`
- **Tests**: `tests/` (Python integration) + Rust unit
- **Scripts**: `scripts/` (e2e runner, seed data, utilities)

## Code Style

- **Comments**: Explain why, not what. No fluff or useless comments.
- **Python imports**: Always at top level, never inside functions.
- **Test assertions**: Use precise comparisons (`==`), not loose bounds. For error paths, assert the specific error code and a message fragment — never just `assert "error" in data`. For HTTP errors, assert the exact status code (`== 400`), not a range (`>= 400`).
- **Python integration tests preferred** over Rust-only tests for system behavior.
- **Test style**: Flat `def test_*()` functions, not `class Test*` groupings.

## Playwright MCP Visual Testing

**Preferred approach: HMR dev mode.** For frontend changes, use the dev servers for instant feedback without rebuilding binaries:

```bash
# Terminal 1 — Rust backend (uses cargo run, no binary install needed)
make dev-backend

# Terminal 2 — Vite dev server with HMR at http://localhost:10456
make dev-frontend
```

Frontend changes are reflected instantly. Only use the full binary approach below when you need to validate Rust worker/scheduler changes.

**Full binary approach** (for Rust changes or final validation):

1. **Build first**: `make all` (required — binaries must exist in `bin/`)
2. **Start the system**: `make run` works for most cases. Use `./scripts/e2e.sh --fresh --seed` when you need a clean DB with seeded agents — it also kills stale processes and waits for health.
3. **Use the browser** at `http://localhost:9456` to create executions and verify rendering
4. **Stop with Ctrl+C** when done

Common pitfalls:
- **"unable to open database file"**: `make run` handles this automatically. If running binaries directly (e.g., `./bin/agentbeacon`), do `touch scheduler-9456.db` first.
- **"Text file busy"**: A running process holds the binary. Kill it first: `fuser -k 9456/tcp` or find the PID with `pgrep -f agentbeacon`.
- **Workers fail to start executor**: Executor JS files not built. `make all` handles this, but if you built Rust only (`cargo build`), run `cd executors && npm install && npm run build` separately.
- **Execution stuck at "submitted"**: All workers are occupied with `input-required` executions. Cancel existing executions via API (`curl -X POST http://localhost:9456/api/executions/<id>/cancel`) or the UI to free a worker.
- **Workers not picking up work**: The default poll interval is 5s. Wait at least 10s after creating an execution. `e2e.sh` uses `--worker-poll-interval 1s` for faster pickup.

To run the automated Playwright E2E suite instead: `make test-e2e`
