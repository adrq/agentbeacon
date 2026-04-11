#!/bin/bash
# Run E2E tests against the scheduler.
#
# Usage:
#   ./scripts/e2e.sh
#   AGENTBEACON_PORT=9457 ./scripts/e2e.sh
set -e

AGENTBEACON_PORT="${AGENTBEACON_PORT:-9456}"
SCHEDULER_URL="http://localhost:${AGENTBEACON_PORT}"
DB_PATH="scheduler-${AGENTBEACON_PORT}.db"

if [[ ! -f bin/agentbeacon ]]; then
    echo "==> Building..."
    make all
fi

BACKEND_PID=""
cleanup() {
    echo ""
    if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "==> Stopping scheduler..."
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    echo "==> Done."
}
trap cleanup EXIT

# Kill any stale process on this port
if fuser "${AGENTBEACON_PORT}/tcp" &>/dev/null; then
    echo "==> Killing stale process on port ${AGENTBEACON_PORT}..."
    fuser -k "${AGENTBEACON_PORT}/tcp" 2>/dev/null || true
    sleep 1
fi

touch "$DB_PATH"

echo "==> Starting scheduler on port ${AGENTBEACON_PORT}..."
./bin/agentbeacon --max-workers 0 --port "$AGENTBEACON_PORT" 2>&1 &
BACKEND_PID=$!

echo "==> Waiting for health check..."
deadline=$((SECONDS + 30))
until curl -sf "${SCHEDULER_URL}/api/health" > /dev/null 2>&1; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "ERROR: scheduler died during startup"
        exit 1
    fi
    if [[ $SECONDS -ge $deadline ]]; then
        echo "ERROR: scheduler did not become healthy within 30s"
        exit 1
    fi
    sleep 0.5
done
echo "==> Scheduler ready."

echo "==> Running E2E specs..."
cd web && API_URL="$SCHEDULER_URL" BASE_URL="$SCHEDULER_URL" AGENTBEACON_PORT="$AGENTBEACON_PORT" \
    npx playwright test tests/e2e/
