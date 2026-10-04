# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import threading
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


def _queued(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]


def _session(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT desired, outcome, parent_notified FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()


def _stage_crashed_child(db_url, exec_id, agent_id, root_id, child_id, worker_id):
    prompt = json.dumps(
        {"message": {"role": "ROLE_USER", "parts": [{"text": "retry me"}]}}
    )
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, agent_id, parent_session_id, slug, "
            "desired, executor_state, outcome, desired_by, parent_notified, worker_id, "
            "sandbox_policy, cwd) "
            "VALUES (?, ?, ?, ?, ?, 'terminate', 'crashed', 'failed', "
            "'system:crash_unrecoverable', FALSE, ?, 'none', '/tmp')",
            (child_id, exec_id, agent_id, root_id, child_id, worker_id),
        )
        conn.execute(
            "INSERT INTO task_queue (execution_id, session_id, task_payload, source) "
            "VALUES (?, ?, ?, NULL)",
            (exec_id, child_id, prompt),
        )
        conn.commit()


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_recovery_between_the_snapshot_and_finalize_survives(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="finalize-race")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync",
            json={"worker_id": "w-finalize"},
            timeout=30,
        )
        _stage_crashed_child(
            test_database, exec_id, agent_id, root_id, "race-child", "w-finalize"
        )
        assert _queued(test_database, "race-child") == 1
        parent_queued_before = _queued(test_database, root_id)

        result = {}

        def sync():
            result["response"] = httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-finalize"},
                timeout=60,
            )

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )

            thread = threading.Thread(target=sync)
            thread.start()
            time.sleep(2)

            holder.execute(
                "UPDATE sessions SET desired = 'run', outcome = NULL, "
                "executor_state = 'unassigned', worker_id = NULL, "
                "recovery_attempts = 0 WHERE id = ?",
                ("race-child",),
            )
            holder.commit()

        thread.join(timeout=60)
        assert not thread.is_alive()

        assert _queued(test_database, "race-child") == 1, (
            "the recovered prompt was drained"
        )
        desired, outcome, notified = _session(test_database, "race-child")
        assert (desired, outcome) == ("run", None)
        assert not notified, "parent_notified was set"
        assert _queued(test_database, root_id) == parent_queued_before, (
            "the parent was told about a crash that had been recovered"
        )
