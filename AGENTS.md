# AGENTS.md

Quick orientation for AI coding agents working in this repository.

## Architecture

Three-process system in Rust:

- **Orchestrator** (`agentmaestro`): Spawns and monitors scheduler + workers
- **Scheduler** (`agentmaestro-scheduler`): REST API, workflow engine, web UI (port 9456)
- **Workers** (`agentmaestro-worker`): Execute workflow nodes in parallel

Frontend: Svelte 5 + TypeScript, embedded in scheduler binary.

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
make dev-frontend   # Terminal 2 → http://localhost:5173

# Run specific test suites
make test           # Rust tests only
uv run pytest -v    # Python integration tests

# Before committing changes
git add <files>
make pre-commit     # Run pre-commit hooks on staged files
git commit
```

## Project Structure

- **Rust binaries**: `orchestrator/`, `scheduler/`, `worker/`, `common/`
- **Frontend**: `web/` (Svelte 5 + TypeScript)
- **Docs & schemas**: `docs/` (workflow, A2A, worker protocols)
- **Examples**: `examples/` (workflow YAML files)
- **Tests**: `tests/` (Python integration) + Rust unit

## Key Documentation

All specs in `docs/`


## Code Style

- **Comments**: Explain why, not what. No fluff or useless comments.
- **Python imports**: Always at top level, never inside functions.
- **Test assertions**: Use precise comparisons (`==`), not loose bounds.
