# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import threading
import time

import httpx
import pytest

from tests.dual_backend import DUAL_BACKEND

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

TERMINAL_OUTCOMES = {"completed", "canceled", "failed"}


class _EventInsertFails:
    def __init__(self, db_url, event_type):
        self.db_url = db_url
        self.event_type = event_type

    def __enter__(self):
        with db_conn(self.db_url) as conn:
            if self.db_url.startswith("postgres"):
                conn.execute(
                    "CREATE OR REPLACE FUNCTION failpoint_events() RETURNS trigger AS $$ "
                    "BEGIN RAISE EXCEPTION 'failpoint'; END; $$ LANGUAGE plpgsql"
                )
                conn.execute(
                    f"CREATE TRIGGER failpoint_events_trigger BEFORE INSERT ON events "
                    f"FOR EACH ROW WHEN (NEW.event_type = '{self.event_type}') "
                    f"EXECUTE FUNCTION failpoint_events()"
                )
            else:
                conn.execute(
                    f"CREATE TRIGGER failpoint_events_trigger BEFORE INSERT ON events "
                    f"WHEN NEW.event_type = '{self.event_type}' "
                    f"BEGIN SELECT RAISE(ABORT, 'failpoint'); END"
                )
            conn.commit()
        return self

    def __exit__(self, *_exc):
        with db_conn(self.db_url) as conn:
            if self.db_url.startswith("postgres"):
                conn.execute(
                    "DROP TRIGGER IF EXISTS failpoint_events_trigger ON events"
                )
                conn.execute("DROP FUNCTION IF EXISTS failpoint_events()")
            else:
                conn.execute("DROP TRIGGER IF EXISTS failpoint_events_trigger")
            conn.commit()


def _session_row(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT desired, outcome, executor_state FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()


def _events(db_url, exec_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE execution_id = ?", (exec_id,)
        ).fetchone()[0]


def _queued(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]


@DUAL_BACKEND
def test_a_stop_that_cannot_record_itself_does_not_happen(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="failpoint-stop")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        before = _session_row(test_database, session_id)
        events_before = _events(test_database, exec_id)

        with _EventInsertFails(test_database, "state_change"):
            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/stop", timeout=30
            )
        assert resp.status_code == 500, resp.text
        assert "persist" in resp.text, resp.text

        assert _session_row(test_database, session_id) == before
        assert _events(test_database, exec_id) == events_before


@DUAL_BACKEND
def test_a_terminate_that_cannot_record_itself_does_not_happen(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="failpoint-terminate")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        before = _session_row(test_database, session_id)
        events_before = _events(test_database, exec_id)

        with _EventInsertFails(test_database, "state_change"):
            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
            )
        assert resp.status_code == 500, resp.text
        assert "persist" in resp.text, resp.text

        assert _session_row(test_database, session_id) == before
        assert _events(test_database, exec_id) == events_before
        with db_conn(test_database) as conn:
            execution = conn.execute(
                "SELECT desired, outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()
        assert execution == ("run", None), execution


@DUAL_BACKEND
def test_a_recovery_that_cannot_record_itself_does_not_happen(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="failpoint-recover")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'failed', "
                "executor_state = 'crashed', agent_session_id = 'as-1', cwd = '/tmp' "
                "WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        before = _session_row(test_database, session_id)
        events_before = _events(test_database, exec_id)
        queued_before = _queued(test_database, session_id)

        with _EventInsertFails(test_database, "state_change"):
            resp = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{session_id}/recover",
                json={"message": "try again"},
                timeout=30,
            )
        assert resp.status_code == 500, resp.text
        assert "persist" in resp.text, resp.text

        assert _session_row(test_database, session_id) == before
        assert _events(test_database, exec_id) == events_before
        assert _queued(test_database, session_id) == queued_before


@DUAL_BACKEND
def test_a_crash_notice_that_cannot_record_itself_is_not_claimed(test_database):
    prompt = json.dumps(
        {"message": {"role": "ROLE_USER", "parts": [{"text": "retry me"}]}}
    )
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="failpoint-notice")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-fp"}, timeout=30
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, agent_id, parent_session_id, slug, "
                "desired, executor_state, outcome, desired_by, parent_notified, worker_id, "
                "sandbox_policy) VALUES (?, ?, ?, ?, ?, 'terminate', 'crashed', 'failed', "
                "'system:crash_unrecoverable', FALSE, 'w-fp', 'none')",
                ("fp-child", exec_id, agent_id, root_id, "fp-child"),
            )
            conn.execute(
                "INSERT INTO task_queue (execution_id, session_id, task_payload, source) "
                "VALUES (?, ?, ?, NULL)",
                (exec_id, "fp-child", prompt),
            )
            conn.commit()

        with _EventInsertFails(test_database, "platform"):
            httpx.post(
                f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-fp"}, timeout=30
            )

        with db_conn(test_database) as conn:
            notified = conn.execute(
                "SELECT parent_notified FROM sessions WHERE id = ?", ("fp-child",)
            ).fetchone()[0]
        assert not notified, "parent_notified was set"
        assert _queued(test_database, root_id) == 0, "a notice was enqueued anyway"
        assert _queued(test_database, "fp-child") == 1


@DUAL_BACKEND
def test_an_automatic_retry_that_cannot_record_itself_does_not_happen(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="failpoint-retry")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-retry"}, timeout=30
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET executor_state = 'crashed', worker_id = 'w-retry', "
                "agent_session_id = 'as-1', recovery_attempts = 0, cwd = '/tmp' WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        events_before = _events(test_database, exec_id)

        with _EventInsertFails(test_database, "state_change"):
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-retry"},
                timeout=30,
            )

        with db_conn(test_database) as conn:
            state, attempts = conn.execute(
                "SELECT executor_state, recovery_attempts FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert (state, attempts) == ("crashed", 0), (state, attempts)
        assert _events(test_database, exec_id) == events_before


@DUAL_BACKEND
def test_an_automatic_retry_records_itself_before_anything_can_follow(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="retry-ordering")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-order"}, timeout=30
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET executor_state = 'crashed', worker_id = 'w-order', "
                "agent_session_id = 'as-1', recovery_attempts = 0, cwd = '/tmp' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-order"}, timeout=30
        )

        with db_conn(test_database) as conn:
            state, attempts = conn.execute(
                "SELECT executor_state, recovery_attempts FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change' "
                "ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        assert (state, attempts) == ("unassigned", 1), (state, attempts)
        retries = [
            json.loads(row[0])
            for row in rows
            if json.loads(row[0]).get("executor_state") == "unassigned"
        ]
        assert len(retries) == 1, rows
        assert retries[0]["recovery_attempt"] == attempts


def _platform_messages(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform' "
            "ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [json.loads(row[0]).get("message", "") for row in rows]


def _state_changes(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change' "
            "ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _report_under_lock(
    ctx, test_database, exec_id, session_id, worker_id, state, mutate
):
    result = {}

    def report():
        result["response"] = httpx.post(
            f"{ctx['url']}/api/worker/sync",
            json={
                "worker_id": worker_id,
                "executor_report": {"session_id": session_id, "executor_state": state},
            },
            timeout=60,
        )

    with db_conn(test_database) as holder:
        holder.execute("SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,))
        thread = threading.Thread(target=report)
        thread.start()
        time.sleep(2)
        assert thread.is_alive(), "the report was expected to wait for the lock"
        mutate(holder)
        holder.commit()

    thread.join(timeout=60)
    assert not thread.is_alive()
    return result["response"]


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_message_delivered_before_the_crash_lands_still_warns(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="feed-vs-crash")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-feed', executor_state = 'running', "
                "command_has_payload = FALSE WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        def deliver(holder):
            holder.execute(
                "UPDATE sessions SET command_token = 'tok-1', command_type = 'feed_turn', "
                "command_has_payload = TRUE WHERE id = ?",
                (session_id,),
            )

        _report_under_lock(
            ctx, test_database, exec_id, session_id, "w-feed", "crashed", deliver
        )

        assert any(
            "may have been lost" in m
            for m in _platform_messages(test_database, session_id)
        ), "no 'may have been lost' platform message was recorded"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_an_idle_report_resets_a_budget_spent_while_it_waited(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="retry-vs-idle")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-idle', executor_state = 'running', "
                "recovery_attempts = 0 WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        def spend_budget(holder):
            holder.execute(
                "UPDATE sessions SET recovery_attempts = 2 WHERE id = ?", (session_id,)
            )

        _report_under_lock(
            ctx, test_database, exec_id, session_id, "w-idle", "idle", spend_budget
        )

        with db_conn(test_database) as conn:
            attempts = conn.execute(
                "SELECT recovery_attempts FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
        assert attempts == 0, "the budget stayed spent after the session went idle"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_state_change_that_happened_while_waiting_is_still_recorded(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="idle-vs-detect")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-dedup', executor_state = 'idle' "
                "WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        before = len(_state_changes(test_database, session_id))

        def start_running(holder):
            holder.execute(
                "UPDATE sessions SET executor_state = 'running' WHERE id = ?",
                (session_id,),
            )

        _report_under_lock(
            ctx, test_database, exec_id, session_id, "w-dedup", "idle", start_running
        )

        emitted = _state_changes(test_database, session_id)[before:]
        assert [e for e in emitted if e.get("executor_state") == "idle"], (
            "the running-to-idle transition was recorded nowhere"
        )


@DUAL_BACKEND
def test_a_lost_crash_advisory_does_not_stop_the_crash_being_detected(test_database):
    with scheduler_context(
        db_url=test_database,
        env={
            "AGENTBEACON_LIVENESS_INTERVAL_SECS": "1",
            "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
            "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "1",
        },
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="advisory-crash")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-gone', executor_state = 'running', "
                "command_token = 'tok', command_type = 'feed_turn', "
                "command_has_payload = TRUE WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        with _EventInsertFails(test_database, "platform"):
            deadline = time.time() + 30
            state, worker = None, None
            while time.time() < deadline:
                with db_conn(test_database) as conn:
                    state, worker = conn.execute(
                        "SELECT executor_state, worker_id FROM sessions WHERE id = ?",
                        (session_id,),
                    ).fetchone()
                if state == "crashed":
                    break
                time.sleep(0.5)

        assert (state, worker) == ("crashed", None), (state, worker)
        assert any(
            e.get("executor_state") == "crashed"
            for e in _state_changes(test_database, session_id)
        )
        assert _platform_messages(test_database, session_id) == []


@DUAL_BACKEND
def test_a_lost_failure_advisory_does_not_stop_the_failure(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="advisory-failure")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-adv"}, timeout=30
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-adv', executor_state = 'crashed', "
                "agent_session_id = 'as-1', recovery_attempts = 9, cwd = '/tmp' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        with _EventInsertFails(test_database, "platform"):
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-adv"},
                timeout=30,
            )

        with db_conn(test_database) as conn:
            outcome = conn.execute(
                "SELECT outcome FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
        assert outcome == "failed", outcome
        assert _platform_messages(test_database, session_id) == []


@DUAL_BACKEND
def test_a_dead_worker_holding_a_payload_command_warns(test_database):
    with scheduler_context(
        db_url=test_database,
        env={"AGENTBEACON_MAX_WORKERS": "0", "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "1"},
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sweep-advisory")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-swept', executor_state = 'running', "
                "command_token = 'tok', command_type = 'feed_turn', "
                "command_has_payload = TRUE WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        time.sleep(2)

        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-other"}, timeout=60
        )

        with db_conn(test_database) as conn:
            state = conn.execute(
                "SELECT executor_state FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
        assert state == "crashed", state
        assert any(
            "may have been lost" in m
            for m in _platform_messages(test_database, session_id)
        ), "the dropped message was never reported"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_repair_does_not_undo_a_recovery_that_won_the_lock(test_database):
    with scheduler_context(
        db_url=test_database,
        env={"AGENTBEACON_MAX_WORKERS": "0", "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "1"},
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="repair-vs-recover")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'run', outcome = 'failed', "
                "executor_state = 'crashed', agent_session_id = 'as-1', cwd = '/tmp' "
                "WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        time.sleep(2)

        def sweep():
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-repair"},
                timeout=60,
            )

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )
            thread = threading.Thread(target=sweep)
            thread.start()
            time.sleep(2)

            holder.execute(
                "UPDATE sessions SET desired = 'run', outcome = NULL, "
                "executor_state = 'unassigned', recovery_attempts = 0 WHERE id = ?",
                (session_id,),
            )
            holder.commit()

        thread.join(timeout=60)
        assert not thread.is_alive()

        with db_conn(test_database) as conn:
            desired, outcome = conn.execute(
                "SELECT desired, outcome FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        assert (desired, outcome) == ("run", None), (desired, outcome)


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_an_idle_report_before_the_verdict_lands_stops_the_failure(test_database):
    with scheduler_context(
        db_url=test_database,
        env={
            "AGENTBEACON_MAX_WORKERS": "0",
            "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "600",
        },
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="verdict-vs-idle")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-verdict"}, timeout=30
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-verdict', executor_state = 'crashed', "
                "agent_session_id = 'as-1', recovery_attempts = 9, cwd = '/tmp' WHERE id = ?",
                (root_id,),
            )
            conn.commit()

        def verdict():
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-verdict"},
                timeout=60,
            )

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )
            thread = threading.Thread(target=verdict)
            thread.start()
            time.sleep(2)
            assert thread.is_alive(), "the verdict was expected to wait for the lock"

            holder.execute(
                "UPDATE sessions SET executor_state = 'idle', recovery_attempts = 0 "
                "WHERE id = ?",
                (root_id,),
            )
            holder.commit()

        thread.join(timeout=60)
        assert not thread.is_alive()

        with db_conn(test_database) as conn:
            desired, outcome, state = conn.execute(
                "SELECT desired, outcome, executor_state FROM sessions WHERE id = ?",
                (root_id,),
            ).fetchone()
            execution = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert outcome is None, (desired, outcome, state)
        assert execution is None, f"the execution outcome was set to {execution!r}"
        assert not any(
            "failed permanently" in m
            for m in _platform_messages(test_database, root_id)
        ), "a 'failed permanently' platform message was recorded"


@DUAL_BACKEND
def test_two_detectors_produce_one_advisory(test_database):
    with scheduler_context(
        db_url=test_database,
        env={"AGENTBEACON_MAX_WORKERS": "0", "AGENTBEACON_HEARTBEAT_TIMEOUT_SECS": "1"},
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="two-detectors")
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-dead', executor_state = 'running', "
                "command_token = 'tok', command_type = 'feed_turn', "
                "command_has_payload = TRUE WHERE id = ?",
                (session_id,),
            )
            conn.commit()
        time.sleep(2)

        def sweep(worker):
            httpx.post(
                f"{ctx['url']}/api/worker/sync", json={"worker_id": worker}, timeout=60
            )

        threads = [
            threading.Thread(target=sweep, args=(f"w-sweeper-{i}",)) for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive()

        messages = [
            m
            for m in _platform_messages(test_database, session_id)
            if "may have been lost" in m
        ]
        assert messages, "the dropped message was never reported"


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_a_stop_landing_before_a_retry_leaves_the_session_parked(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(
            ctx["db_url"], name="stop-vs-retry", agent_type="claude_sdk"
        )
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        httpx.post(
            f"{ctx['url']}/api/worker/sync", json={"worker_id": "w-retry"}, timeout=30
        )
        with db_conn(test_database) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_id,))
            conn.execute(
                "UPDATE sessions SET worker_id = 'w-retry', executor_state = 'crashed', "
                "agent_session_id = 'as-1', recovery_attempts = 0, cwd = '/tmp' WHERE id = ?",
                (root_id,),
            )
            conn.commit()

        def retry():
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-retry"},
                timeout=60,
            )

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )
            thread = threading.Thread(target=retry)
            thread.start()
            time.sleep(2)
            assert thread.is_alive(), "the retry was expected to wait for the lock"

            holder.execute(
                "UPDATE sessions SET desired = 'stop', desired_by = 'user' WHERE id = ?",
                (root_id,),
            )
            holder.commit()

        thread.join(timeout=60)
        assert not thread.is_alive()

        with db_conn(test_database) as conn:
            desired, state, attempts = conn.execute(
                "SELECT desired, executor_state, recovery_attempts FROM sessions WHERE id = ?",
                (root_id,),
            ).fetchone()
        assert (desired, state, attempts) == ("stop", "crashed", 0), (
            desired,
            state,
            attempts,
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text
        with db_conn(test_database) as conn:
            outcome = conn.execute(
                "SELECT outcome FROM sessions WHERE id = ?", (root_id,)
            ).fetchone()[0]
        assert outcome == "completed", outcome


@DUAL_BACKEND
def test_an_outcome_that_cannot_be_derived_is_not_written(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="derivation-fault")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with db_conn(test_database) as conn:
            conn.execute("ALTER TABLE task_queue RENAME TO task_queue_hidden")
            conn.commit()
        try:
            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
            )
            assert resp.status_code == 500, resp.text
        finally:
            with db_conn(test_database) as conn:
                conn.execute("ALTER TABLE task_queue_hidden RENAME TO task_queue")
                conn.commit()

        with db_conn(test_database) as conn:
            session_outcome = conn.execute(
                "SELECT outcome FROM sessions WHERE id = ?", (root_id,)
            ).fetchone()[0]
            execution_outcome = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert session_outcome is None, session_outcome
        assert execution_outcome is None, execution_outcome

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text
        with db_conn(test_database) as conn:
            execution_outcome = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert execution_outcome in TERMINAL_OUTCOMES, execution_outcome
