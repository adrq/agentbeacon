#!/bin/bash
# E2E test environment launcher.
# Usage:
#   ./scripts/e2e.sh [--fresh]           # Start system for manual testing
#   ./scripts/e2e.sh --run-tests         # Start system, run Playwright, exit
#   ./scripts/e2e.sh --fresh --run-tests # Fresh DB + run Playwright
#
# Supports AGENTBEACON_PORT env var for multi-instance usage:
#   AGENTBEACON_PORT=9457 ./scripts/e2e.sh
set -e

RUN_TESTS=false
FRESH=false
for arg in "$@"; do
    case "$arg" in
        --run-tests) RUN_TESTS=true ;;
        --fresh) FRESH=true ;;
    esac
done

AGENTBEACON_PORT="${AGENTBEACON_PORT:-9456}"
DB_PATH="scheduler-${AGENTBEACON_PORT}.db"
SCHEDULER_URL="http://localhost:${AGENTBEACON_PORT}"

if $FRESH; then
    echo "==> Removing old database..."
    rm -f "$DB_PATH" "${DB_PATH}-wal" "${DB_PATH}-shm"
fi

# Kill any stale process on our specific port
if fuser "${AGENTBEACON_PORT}/tcp" &>/dev/null; then
    echo "==> Killing stale process on port ${AGENTBEACON_PORT}..."
    fuser -k "${AGENTBEACON_PORT}/tcp" 2>/dev/null || true
    sleep 1
fi

# SQLite requires the file to exist (sqlx connect doesn't create it)
touch "$DB_PATH"

if [[ ! -f bin/agentbeacon ]]; then
    echo "==> Building..."
    make all
fi

# Worker needs this to find executors/dist/claude-executor.js
export AGENTBEACON_EXECUTORS_DIR="${AGENTBEACON_EXECUTORS_DIR:-$(pwd)/executors/dist}"

echo "==> Starting orchestrator (scheduler + worker) on port ${AGENTBEACON_PORT}..."
./bin/agentbeacon --workers 2 --scheduler-port "$AGENTBEACON_PORT" --worker-poll-interval 1s &
ORCH_PID=$!

cleanup() {
    echo ""
    echo "==> Stopping orchestrator (PID $ORCH_PID)..."
    # SIGTERM lets the orchestrator gracefully stop its children
    kill $ORCH_PID 2>/dev/null || true
    # Give it time to propagate SIGTERM to scheduler + workers
    for i in $(seq 1 5); do
        kill -0 $ORCH_PID 2>/dev/null || break
        sleep 1
    done
    # Force-kill the process group if anything survived
    kill -KILL -- -$ORCH_PID 2>/dev/null || true
    wait $ORCH_PID 2>/dev/null || true
    echo "==> Done."
}
trap cleanup EXIT

echo "==> Waiting for scheduler..."
until curl -sf "${SCHEDULER_URL}/api/health" > /dev/null 2>&1; do
    sleep 0.5
done
echo "==> Scheduler ready."

echo "==> Seeding agents..."
uv run python scripts/seed_agents.py --db-path "$DB_PATH"

if $RUN_TESTS; then
    echo "==> Running Playwright E2E tests..."
    cd web && API_URL="$SCHEDULER_URL" BASE_URL="$SCHEDULER_URL" npx playwright test
else
    cat <<STEPS

============================================
  E2E Test Ready — ${SCHEDULER_URL}
============================================

Run Playwright tests:
  cd web && API_URL=${SCHEDULER_URL} BASE_URL=${SCHEDULER_URL} npx playwright test

Or test manually:
  1. Open ${SCHEDULER_URL}
  2. Click "+ New", select "Demo Agent", type any prompt
  3. Watch the execution lifecycle

Press Ctrl+C to stop.
STEPS

    wait $ORCH_PID
fi
