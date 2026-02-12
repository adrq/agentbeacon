#!/bin/bash
# Manual E2E test launcher.
# Usage: ./scripts/manual_e2e.sh [--fresh]
set -e

DB_PATH="scheduler.db"
SCHEDULER_URL="http://localhost:9456"

if [[ "$1" == "--fresh" ]]; then
    echo "==> Removing old database..."
    rm -f "$DB_PATH"
fi

# Kill any stale agentbeacon processes from a previous unclean exit.
# fuser alone isn't enough — it only kills the scheduler on the port,
# but the orchestrator parent survives and keeps restarting it.
if pgrep -f 'agentbeacon' &>/dev/null; then
    echo "==> Killing stale agentbeacon processes..."
    pkill -TERM -f 'bin/agentbeacon' 2>/dev/null || true
    sleep 2
    pkill -KILL -f 'bin/agentbeacon' 2>/dev/null || true
    sleep 1
fi

# SQLite requires the file to exist (sqlx connect doesn't create it)
touch "$DB_PATH"

if [[ ! -f bin/agentbeacon ]]; then
    echo "==> Building..."
    make all
fi

echo "==> Starting orchestrator (scheduler + worker)..."
./bin/agentbeacon --workers 1 --worker-poll-interval 1s &
ORCH_PID=$!

cleanup() {
    echo ""
    echo "==> Stopping orchestrator..."
    # SIGTERM lets the orchestrator gracefully stop its children
    kill $ORCH_PID 2>/dev/null || true
    # Give it time to propagate SIGTERM to scheduler + workers
    for i in $(seq 1 5); do
        kill -0 $ORCH_PID 2>/dev/null || break
        sleep 1
    done
    # Force-kill entire process tree if anything survived
    pkill -KILL -f 'bin/agentbeacon' 2>/dev/null || true
    wait $ORCH_PID 2>/dev/null || true
    echo "==> Done."
}
trap cleanup EXIT

echo "==> Waiting for scheduler..."
until curl -sf "${SCHEDULER_URL}/api/health" > /dev/null 2>&1; do
    sleep 0.5
done
echo "==> Scheduler ready."

echo "==> Seeding demo agent..."
python3 -c "
import sqlite3, uuid, json
conn = sqlite3.connect('${DB_PATH}')
existing = conn.execute(\"SELECT id FROM agents WHERE name = 'Demo Agent'\").fetchone()
if existing:
    print(f'  Demo Agent already exists: {existing[0]}')
else:
    agent_id = str(uuid.uuid4())
    config = json.dumps({
        'command': 'uv',
        'args': ['run', 'python', '-m', 'agentmaestro.mock_agent', '--mode', 'acp', '--scenario', 'demo'],
        'timeout': 60
    })
    conn.execute(
        'INSERT INTO agents (id, name, description, agent_type, config, enabled) VALUES (?, ?, ?, ?, ?, 1)',
        (agent_id, 'Demo Agent', 'Mock ACP agent for e2e testing', 'acp', config)
    )
    conn.commit()
    print(f'  Created Demo Agent: {agent_id}')
conn.close()
"

cat <<'STEPS'

============================================
  E2E Test Ready — http://localhost:9456
============================================

Test 1: Happy Path with Question (~60s)
  1. Open http://localhost:9456
  2. Click "+ New", select "Demo Agent", type any prompt
  3. Click Start
  4. Watch: submitted -> working, message events appear
  5. Watch: status -> input-required, question banner with 3 options
  6. Select an option, click Submit Answer
  7. Watch: status -> working, more messages, -> completed

Test 2: Error Handling (~30s)
  1. Click "+ New", select "Demo Agent"
  2. Type: EXIT_1
  3. Click Start
  4. Verify: working -> failed, error event in timeline

Test 3: Theme Toggle (~10s)
  1. Click theme toggle — verify light/dark switch

Press Ctrl+C to stop.
STEPS

wait $ORCH_PID
