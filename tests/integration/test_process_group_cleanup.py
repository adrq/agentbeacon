# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import signal
import time

import psutil
from tests.testhelpers import (
    create_execution_via_api,
    orchestrator_context,
)
from tests.mock_agent_helpers import (
    get_session_row,
    seed_acp_mock_agent,
)


def _poll_until(predicate, timeout=10, interval=0.3):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _is_alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _get_worker_children(orchestrator_pid):
    try:
        parent = psutil.Process(orchestrator_pid)
        return [
            c
            for c in parent.children(recursive=False)
            if "agentbeacon-worker" in " ".join(c.cmdline() or [])
        ]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def test_scheduler_crash_kills_workers():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="60s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: len(_get_worker_children(orch["orchestrator_pid"])) >= 1,
            timeout=20,
        ), "Worker did not spawn within 20s"

        worker_pids = [w.pid for w in _get_worker_children(orch["orchestrator_pid"])]
        assert len(worker_pids) >= 1

        os.kill(orch["orchestrator_pid"], signal.SIGKILL)

        assert _poll_until(
            lambda: all(not _is_alive(pid) for pid in worker_pids),
            timeout=5,
        ), f"Worker PIDs {worker_pids} still alive after scheduler SIGKILL"


def test_worker_crash_kills_executor():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="60s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("worker_id")
            is not None,
            timeout=20,
        ), "Session never got a worker"

        workers = _get_worker_children(orch["orchestrator_pid"])
        assert len(workers) >= 1
        worker = workers[0]
        worker_pid = worker.pid

        assert _poll_until(
            lambda: len(psutil.Process(worker_pid).children(recursive=True)) >= 1,
            timeout=15,
        ), "Worker never spawned an executor child"

        executor_pids = [
            c.pid for c in psutil.Process(worker_pid).children(recursive=True)
        ]
        assert len(executor_pids) >= 1

        os.kill(worker_pid, signal.SIGKILL)

        assert _poll_until(
            lambda: all(not _is_alive(pid) for pid in executor_pids),
            timeout=5,
        ), f"Executor PIDs {executor_pids} still alive after worker SIGKILL"


def test_graceful_shutdown_kills_executor_subtree():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="60s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("worker_id")
            is not None,
            timeout=20,
        ), "Session never got a worker"

        workers = _get_worker_children(orch["orchestrator_pid"])
        assert len(workers) >= 1
        worker_pid = workers[0].pid

        assert _poll_until(
            lambda: len(psutil.Process(worker_pid).children(recursive=True)) >= 1,
            timeout=15,
        ), "Worker never spawned an executor child"

        all_pids = [worker_pid]
        all_pids.extend(
            c.pid for c in psutil.Process(worker_pid).children(recursive=True)
        )
        assert len(all_pids) >= 2, (
            f"Expected worker + at least 1 executor child, got {len(all_pids)} PIDs"
        )

        orch["orchestrator"].send_signal(signal.SIGTERM)

        assert _poll_until(
            lambda: all(not _is_alive(pid) for pid in all_pids),
            timeout=15,
        ), f"Subtree PIDs {all_pids} still alive after graceful shutdown"
