# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import signal
import tempfile
import time

import psutil
import requests
from tests.testhelpers import (
    orchestrator_context,
    scheduler_context,
    create_execution_via_api,
    db_conn,
)
from tests.mock_agent_helpers import (
    seed_acp_mock_agent,
    seed_acp_scenario_agent,
    get_session_row,
    set_session_fields,
    insert_task,
    set_config,
)


def _poll_until(predicate, timeout=30, interval=0.5):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _count_worker_children(orchestrator_pid):
    try:
        parent = psutil.Process(orchestrator_pid)
        children = parent.children(recursive=False)
        return len(
            [c for c in children if "agentbeacon-worker" in " ".join(c.cmdline() or [])]
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0


def test_dynamic_worker_spawn():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        assert _count_worker_children(orch["orchestrator_pid"]) == 0

        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: _count_worker_children(orch["orchestrator_pid"]) >= 1,
            timeout=20,
        ), "Worker did not spawn within 20s"

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("executor_state")
            == "idle",
            timeout=30,
        ), "Session did not reach idle state"


def test_worker_reuse():
    with orchestrator_context(
        max_workers=1,
        worker_poll_interval="1s",
        idle_timeout="60s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])

        exec_id1, session_id1 = create_execution_via_api(orch["url"], agent_id)
        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id1).get("executor_state")
            == "idle",
            timeout=30,
        ), "First session did not complete"

        requests.post(
            f"{orch['url']}/api/v1/sessions/{session_id1}/terminate", timeout=5
        )

        time.sleep(1)
        workers_after_first = _count_worker_children(orch["orchestrator_pid"])
        assert workers_after_first == 1, (
            f"Expected 1 worker after first session, got {workers_after_first}"
        )

        exec_id2, session_id2 = create_execution_via_api(orch["url"], agent_id)
        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id2).get("executor_state")
            == "idle",
            timeout=30,
        ), "Second session did not complete"

        assert _count_worker_children(orch["orchestrator_pid"]) == 1


def test_idle_timeout_exit():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="10s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("executor_state")
            == "idle",
            timeout=30,
        ), "Session did not reach idle"

        requests.post(
            f"{orch['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("worker_id")
            is None,
            timeout=30,
        ), "Finalize did not release worker"

        assert _poll_until(
            lambda: _count_worker_children(orch["orchestrator_pid"]) == 0,
            timeout=30,
        ), "Worker did not self-terminate after idle timeout"


def test_max_workers_limit():
    with orchestrator_context(
        max_workers=1,
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])

        exec_id1, session_id1 = create_execution_via_api(orch["url"], agent_id)
        exec_id2, session_id2 = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: _count_worker_children(orch["orchestrator_pid"]) >= 1,
            timeout=20,
        ), "No worker spawned"

        time.sleep(5)
        assert _count_worker_children(orch["orchestrator_pid"]) <= 1, (
            "More than 1 worker spawned despite --max-workers 1"
        )


def test_kill_confirmation_on_crash():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("worker_id")
            is not None,
            timeout=20,
        ), "Session never got a worker"

        original_worker_id = get_session_row(orch["db_url"], session_id)["worker_id"]

        parent = psutil.Process(orch["orchestrator_pid"])
        workers = [
            c
            for c in parent.children(recursive=False)
            if "agentbeacon-worker" in " ".join(c.cmdline() or [])
        ]
        assert len(workers) >= 1, "Expected at least 1 worker"
        workers[0].send_signal(signal.SIGKILL)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("worker_id")
            != original_worker_id,
            timeout=30,
            interval=1,
        ), "DetectCrash did not clear worker_id after killing worker"


def test_burst_spawn():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])

        sessions = []
        for _ in range(3):
            exec_id, session_id = create_execution_via_api(orch["url"], agent_id)
            sessions.append(session_id)

        assert _poll_until(
            lambda: _count_worker_children(orch["orchestrator_pid"]) >= 3,
            timeout=30,
        ), f"Expected 3 workers, got {_count_worker_children(orch['orchestrator_pid'])}"


def test_max_spawn_per_tick():
    orig = os.environ.get("AGENTBEACON_MAX_SPAWN_PER_TICK")
    os.environ["AGENTBEACON_MAX_SPAWN_PER_TICK"] = "1"
    try:
        with orchestrator_context(
            worker_poll_interval="1s",
            idle_timeout="30s",
        ) as orch:
            agent_id = seed_acp_mock_agent(orch["db_url"])

            for _ in range(3):
                create_execution_via_api(orch["url"], agent_id)

            time.sleep(3)
            count_after_first_tick = _count_worker_children(orch["orchestrator_pid"])
            assert count_after_first_tick <= 2, (
                f"Rate limiting failed: {count_after_first_tick} workers after ~3s "
                f"(expected <= 2 with max_spawn_per_tick=1)"
            )

            assert _poll_until(
                lambda: _count_worker_children(orch["orchestrator_pid"]) >= 3,
                timeout=30,
            ), "Not all 3 workers spawned"
    finally:
        if orig is None:
            os.environ.pop("AGENTBEACON_MAX_SPAWN_PER_TICK", None)
        else:
            os.environ["AGENTBEACON_MAX_SPAWN_PER_TICK"] = orig


def test_finalize_releases_worker():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("executor_state")
            == "idle",
            timeout=30,
        ), "Session did not reach idle"

        resp = requests.post(
            f"{orch['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )
        assert resp.status_code == 200

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("worker_id")
            is None,
            timeout=15,
        ), "worker_id not cleared after finalize"


def test_delegate_spawns_worker():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        lead_id = seed_acp_scenario_agent(
            orch["db_url"], "lead", "delegate", delegate_to="child-agent"
        )
        child_id = seed_acp_scenario_agent(orch["db_url"], "child-agent", "end-turn")

        exec_id, lead_sid = create_execution_via_api(
            orch["url"], lead_id, "delegate a task"
        )

        with db_conn(orch["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_id),
            )
            conn.commit()

        def _has_child_session():
            with db_conn(orch["db_url"]) as conn:
                rows = conn.execute(
                    "SELECT id FROM sessions WHERE execution_id = ? AND id != ?",
                    (exec_id, lead_sid),
                ).fetchall()
                return len(rows) > 0

        assert _poll_until(_has_child_session, timeout=30), (
            "Child session never created"
        )

        assert _poll_until(
            lambda: _count_worker_children(orch["orchestrator_pid"]) >= 2,
            timeout=20,
        ), (
            f"Expected >= 2 workers, got {_count_worker_children(orch['orchestrator_pid'])}"
        )


def test_crash_recovery_spawns_worker():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="30s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id1, session_id1 = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id1).get("worker_id")
            is not None,
            timeout=20,
        ), "First session never got a worker"

        parent = psutil.Process(orch["orchestrator_pid"])
        workers = [
            c
            for c in parent.children(recursive=False)
            if "agentbeacon-worker" in " ".join(c.cmdline() or [])
        ]
        assert len(workers) >= 1
        killed_pid = workers[0].pid
        os.kill(killed_pid, signal.SIGKILL)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id1).get("worker_id")
            is None,
            timeout=30,
            interval=1,
        ), "DetectCrash did not fire"

        exec_id2, session_id2 = create_execution_via_api(orch["url"], agent_id)
        assert _poll_until(
            lambda: _count_worker_children(orch["orchestrator_pid"]) >= 1,
            timeout=20,
        ), "New worker did not spawn for new work after crash"


def test_startup_recovery_workerless():
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db.close()
    db_url = f"sqlite:{temp_db.name}?mode=rwc"

    try:
        with scheduler_context(db_url=db_url) as ctx:
            agent_id = seed_acp_mock_agent(ctx["db_url"], name="recovery-agent")
            exec_id, session_id = create_execution_via_api(
                ctx["url"], agent_id, "recover me"
            )

            set_session_fields(
                ctx["db_url"],
                session_id,
                desired="run",
                executor_state="crashed",
                worker_id=None,
                command_token=None,
                command_type=None,
                command_at=None,
                command_has_payload=False,
                recovery_attempts=0,
            )
            insert_task(ctx["db_url"], exec_id, session_id, "pending prompt")
            set_config(ctx["db_url"], "restart.pause_sessions", "false")

        with orchestrator_context(
            db_url=db_url,
            worker_poll_interval="1s",
            idle_timeout="30s",
        ) as orch:
            assert _poll_until(
                lambda: get_session_row(orch["db_url"], session_id).get(
                    "executor_state"
                )
                != "crashed",
                timeout=30,
            ), "Recovery did not fire for crashed workerless session"

            assert _poll_until(
                lambda: _count_worker_children(orch["orchestrator_pid"]) >= 1,
                timeout=20,
            ), "No worker spawned for recovered session"
    finally:
        os.unlink(temp_db.name)


def test_idle_timeout_disabled():
    with orchestrator_context(
        worker_poll_interval="1s",
        idle_timeout="0s",
    ) as orch:
        agent_id = seed_acp_mock_agent(orch["db_url"])
        exec_id, session_id = create_execution_via_api(orch["url"], agent_id)

        assert _poll_until(
            lambda: get_session_row(orch["db_url"], session_id).get("executor_state")
            == "idle",
            timeout=30,
        ), "Session did not reach idle"

        requests.post(
            f"{orch['url']}/api/v1/sessions/{session_id}/terminate", timeout=5
        )

        time.sleep(15)
        assert _count_worker_children(orch["orchestrator_pid"]) >= 1, (
            "Worker self-terminated despite --idle-timeout 0"
        )
