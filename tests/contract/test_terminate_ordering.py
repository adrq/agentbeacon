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

TERMINAL_OUTCOMES = {"completed", "failed", "canceled"}


def _add_child(db_url, exec_id, agent_id, parent_id, child_id, executor_state):
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, agent_id, parent_session_id, slug, "
            "desired, executor_state, sandbox_policy) VALUES (?, ?, ?, ?, ?, 'run', ?, 'none')",
            (child_id, exec_id, agent_id, parent_id, child_id, executor_state),
        )
        conn.commit()


def _state_changes(db_url, exec_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT id, session_id, payload FROM events WHERE execution_id = ? "
            "AND event_type = 'state_change' ORDER BY id ASC",
            (exec_id,),
        ).fetchall()
    return [(row[0], row[1], json.loads(row[2])) for row in rows]


def _is_execution_terminal(session_id, payload):
    return session_id is None and payload.get("outcome") in TERMINAL_OUTCOMES


class _StreamReader:
    def __init__(self, url, exec_id):
        self._url = f"{url}/api/v1/executions/{exec_id}/events/stream"
        self.lines = []
        self.closed = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with httpx.stream("GET", self._url, timeout=60) as response:
                for line in response.iter_lines():
                    self.lines.append(line)
        except httpx.HTTPError:
            pass
        finally:
            self.closed = True

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if any(line.startswith("event: position") for line in self.lines):
                return self
            time.sleep(0.05)
        raise AssertionError("the stream never announced its position")

    def __exit__(self, *_exc):
        pass

    def records(self):
        out = []
        for line in self.lines:
            if not line.startswith("data: ") or line.startswith('data: {"position"'):
                continue
            try:
                record = json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                continue
            if "event_type" in record:
                out.append(record)
        return out

    def wait_for_close(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline and not self.closed:
            time.sleep(0.05)
        return self.closed


@DUAL_BACKEND
def test_terminate_writes_the_execution_terminal_row_with_the_root(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="terminate-order")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for index in (1, 2, 3):
            _add_child(
                test_database,
                exec_id,
                agent_id,
                root_id,
                f"order-child-{index}",
                "running",
            )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text

        events = _state_changes(test_database, exec_id)
        terminal = [
            (event_id, session_id)
            for event_id, session_id, payload in events
            if _is_execution_terminal(session_id, payload)
        ]
        assert len(terminal) == 1, events
        terminal_id = terminal[0][0]

        cascaded = {
            session_id: event_id
            for event_id, session_id, payload in events
            if payload.get("desired_by") == "system:cascade"
        }
        assert set(cascaded) == {"order-child-1", "order-child-2", "order-child-3"}
        for session_id, event_id in cascaded.items():
            assert event_id > terminal_id, (
                f"{session_id} was written before the terminal row"
            )
        root_rows = [
            event_id
            for event_id, session_id, payload in events
            if payload.get("desired_by") == "user"
        ]
        assert root_rows and max(root_rows) < terminal_id


@DUAL_BACKEND
def test_the_stream_forwards_the_terminal_event_and_closes(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="terminate-stream")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for index in (1, 2, 3):
            _add_child(
                test_database,
                exec_id,
                agent_id,
                root_id,
                f"stream-child-{index}",
                "running",
            )

        with _StreamReader(ctx["url"], exec_id) as stream:
            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
            )
            assert resp.status_code == 200, resp.text
            assert stream.wait_for_close(), "the stream never closed"

        delivered = stream.records()
        last = delivered[-1]
        assert last["session_id"] is None, last
        assert last["payload"]["outcome"] in TERMINAL_OUTCOMES, last
        assert (
            sum(
                1
                for record in delivered
                if _is_execution_terminal(record["session_id"], record["payload"])
            )
            == 1
        )

        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events",
            params={"limit": 500},
            timeout=30,
        ).json()["items"]
        served = {item["session_id"] for item in page}
        for index in (1, 2, 3):
            assert f"stream-child-{index}" in served, served


@DUAL_BACKEND
def test_a_crashed_child_still_makes_the_execution_canceled(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="terminate-crashed")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(
            test_database, exec_id, agent_id, root_id, "crashed-child", "crashed"
        )
        with db_conn(test_database) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_id,))
            conn.execute(
                "UPDATE sessions SET executor_state = 'idle' WHERE id = ?", (root_id,)
            )
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text

        with db_conn(test_database) as conn:
            outcome = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert outcome == "canceled"

        events = _state_changes(test_database, exec_id)
        terminal = [
            (event_id, payload)
            for event_id, session_id, payload in events
            if _is_execution_terminal(session_id, payload)
        ]
        assert len(terminal) == 1, events
        assert terminal[0][1]["outcome"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_a_terminate_interrupted_before_the_last_write_is_repaired_once(test_database):
    with scheduler_context(
        db_url=test_database,
        env={
            "AGENTBEACON_LIVENESS_INTERVAL_SECS": "1",
            "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
        },
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="terminate-crashgap")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(test_database, exec_id, agent_id, root_id, "gap-child", "running")

        with db_conn(test_database) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_id,))
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'canceled', "
                "executor_state = 'idle' WHERE id IN (?, ?)",
                (root_id, "gap-child"),
            )
            conn.commit()

        deadline = time.time() + 30
        while time.time() < deadline:
            with db_conn(test_database) as conn:
                outcome = conn.execute(
                    "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
                ).fetchone()[0]
            if outcome is not None:
                break
            time.sleep(0.5)
        assert outcome in TERMINAL_OUTCOMES, (
            "the reconciler never repaired the execution"
        )

        time.sleep(4)
        events = _state_changes(test_database, exec_id)
        terminal = [
            event_id
            for event_id, session_id, payload in events
            if _is_execution_terminal(session_id, payload)
        ]
        assert len(terminal) == 1, events


def _stage_first_crash_window(db_url, exec_id, root_id, child_id):
    with db_conn(db_url) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_id,))
        conn.execute(
            "UPDATE sessions SET desired = 'terminate', outcome = 'canceled', "
            "executor_state = 'idle' WHERE id = ?",
            (root_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT desired, outcome FROM sessions WHERE id = ?", (child_id,)
        ).fetchone()
    assert row == ("run", None), row


def _stage_first_crash_window_without_children(db_url, root_id):
    with db_conn(db_url) as conn:
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (root_id,))
        conn.execute(
            "UPDATE sessions SET desired = 'terminate', outcome = 'canceled', "
            "executor_state = 'idle' WHERE id = ?",
            (root_id,),
        )
        conn.commit()


def _assert_terminal_precedes_children(db_url, exec_id, child_ids):
    events = _state_changes(db_url, exec_id)
    terminal = [
        event_id
        for event_id, session_id, payload in events
        if _is_execution_terminal(session_id, payload)
    ]
    assert len(terminal) == 1, events
    for child_id in child_ids:
        written = [
            event_id for event_id, session_id, _p in events if session_id == child_id
        ]
        assert written, f"{child_id} never transitioned: {events}"
        assert min(written) > terminal[0], (
            f"{child_id} was written before the terminal row"
        )


@DUAL_BACKEND
def test_the_execution_handler_repairs_the_first_crash_window(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="gap-exec-handler")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(
            test_database, exec_id, agent_id, root_id, "gap-exec-child", "running"
        )
        _stage_first_crash_window(test_database, exec_id, root_id, "gap-exec-child")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text
        _assert_terminal_precedes_children(test_database, exec_id, ["gap-exec-child"])


@DUAL_BACKEND
def test_the_session_handler_repairs_the_first_crash_window(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="gap-session-handler")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(
            test_database, exec_id, agent_id, root_id, "gap-session-child", "running"
        )
        _stage_first_crash_window(test_database, exec_id, root_id, "gap-session-child")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{root_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text
        _assert_terminal_precedes_children(
            test_database, exec_id, ["gap-session-child"]
        )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_the_reconciler_repairs_the_first_crash_window(test_database):
    with scheduler_context(
        db_url=test_database,
        env={
            "AGENTBEACON_LIVENESS_INTERVAL_SECS": "1",
            "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
        },
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="gap-tick")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(
            test_database, exec_id, agent_id, root_id, "gap-tick-child", "running"
        )
        _stage_first_crash_window(test_database, exec_id, root_id, "gap-tick-child")

        deadline = time.time() + 30
        outcome = None
        while time.time() < deadline:
            with db_conn(test_database) as conn:
                outcome = conn.execute(
                    "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
                ).fetchone()[0]
            if outcome is not None:
                break
            time.sleep(0.5)
        assert outcome in TERMINAL_OUTCOMES, (
            "the reconciler never repaired the execution"
        )

        deadline = time.time() + 30
        while time.time() < deadline:
            with db_conn(test_database) as conn:
                child = conn.execute(
                    "SELECT desired FROM sessions WHERE id = ?", ("gap-tick-child",)
                ).fetchone()[0]
            if child == "terminate":
                break
            time.sleep(0.5)
        _assert_terminal_precedes_children(test_database, exec_id, ["gap-tick-child"])


@DUAL_BACKEND
def test_a_cascade_that_cannot_finish_is_reported(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="cascade-fault")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        other_exec_id, _other_root = create_execution_via_api(
            ctx["url"], agent_id, "other"
        )
        _add_child(test_database, exec_id, agent_id, root_id, "fault-child", "running")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET execution_id = ? WHERE id = ?",
                (other_exec_id, "fault-child"),
            )
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 500, resp.text

        with db_conn(test_database) as conn:
            outcome = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
            child = conn.execute(
                "SELECT desired, outcome FROM sessions WHERE id = ?", ("fault-child",)
            ).fetchone()
        assert outcome in TERMINAL_OUTCOMES, outcome
        assert child == ("run", None), child


@DUAL_BACKEND
def test_a_terminal_row_that_cannot_announce_itself_is_not_committed(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="terminal-no-event")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _stage_first_crash_window_without_children(test_database, root_id)

        with db_conn(test_database) as conn:
            conn.execute("ALTER TABLE events RENAME TO events_hidden")
            conn.commit()
        try:
            resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
            )
            assert resp.status_code == 500, resp.text
        finally:
            with db_conn(test_database) as conn:
                conn.execute("ALTER TABLE events_hidden RENAME TO events")
                conn.commit()

        with db_conn(test_database) as conn:
            outcome = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        assert outcome is None, "a terminal row committed without its event"
        assert [
            event_id
            for event_id, session_id, payload in _state_changes(test_database, exec_id)
            if _is_execution_terminal(session_id, payload)
        ] == []


def _child_ids_in_order(db_url, exec_id, session_ids):
    events = _state_changes(db_url, exec_id)
    return {
        session_id: max(
            [event_id for event_id, sid, _p in events if sid == session_id],
            default=None,
        )
        for session_id in session_ids
    }


@DUAL_BACKEND
def test_a_nested_tree_transitions_entirely_after_the_terminal_row(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="nested-tree")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(test_database, exec_id, agent_id, root_id, "nested-child", "running")
        _add_child(
            test_database,
            exec_id,
            agent_id,
            "nested-child",
            "nested-grandchild",
            "running",
        )
        _add_child(
            test_database,
            exec_id,
            agent_id,
            "nested-grandchild",
            "nested-great-grandchild",
            "running",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text
        _assert_terminal_precedes_children(
            test_database,
            exec_id,
            ["nested-child", "nested-grandchild", "nested-great-grandchild"],
        )


@DUAL_BACKEND
def test_an_already_terminating_child_does_not_hide_its_subtree(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="pre-terminated")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(test_database, exec_id, agent_id, root_id, "pre-child", "running")
        _add_child(
            test_database, exec_id, agent_id, "pre-child", "pre-grandchild", "running"
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'canceled' WHERE id = ?",
                ("pre-child",),
            )
            conn.commit()
            live = conn.execute(
                "SELECT desired, outcome FROM sessions WHERE id = ?",
                ("pre-grandchild",),
            ).fetchone()
        assert live == ("run", None), live

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=30
        )
        assert resp.status_code == 200, resp.text

        with db_conn(test_database) as conn:
            grandchild = conn.execute(
                "SELECT desired, outcome FROM sessions WHERE id = ?",
                ("pre-grandchild",),
            ).fetchone()
        assert grandchild[0] == "terminate", grandchild
        _assert_terminal_precedes_children(test_database, exec_id, ["pre-grandchild"])


def _tick_env():
    return {
        "AGENTBEACON_LIVENESS_INTERVAL_SECS": "1",
        "AGENTBEACON_RECOVERY_GRACE_SECS": "1",
    }


def _wait_for_execution_outcome(db_url, exec_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with db_conn(db_url) as conn:
            outcome = conn.execute(
                "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
            ).fetchone()[0]
        if outcome is not None:
            return outcome
        time.sleep(0.5)
    return None


@DUAL_BACKEND
def test_a_deleted_agent_terminates_the_tree_after_the_terminal_row(test_database):
    with scheduler_context(db_url=test_database, env=_tick_env()) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="deleted-agent-root")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(
            test_database, exec_id, agent_id, root_id, "deleted-child", "running"
        )
        _add_child(
            test_database,
            exec_id,
            agent_id,
            "deleted-child",
            "deleted-grandchild",
            "running",
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE agents SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (agent_id,),
            )
            conn.execute(
                "UPDATE sessions SET executor_state = 'unassigned', worker_id = 'w-deleted' "
                "WHERE id = ?",
                (root_id,),
            )
            conn.commit()

        deadline = time.time() + 30
        outcome = None
        while time.time() < deadline and outcome is None:
            httpx.post(
                f"{ctx['url']}/api/worker/sync",
                json={"worker_id": "w-deleted"},
                timeout=30,
            )
            time.sleep(0.5)
            with db_conn(test_database) as conn:
                outcome = conn.execute(
                    "SELECT outcome FROM executions WHERE id = ?", (exec_id,)
                ).fetchone()[0]
        assert outcome in TERMINAL_OUTCOMES, (
            "the deleted agent never terminated its root"
        )
        _assert_terminal_precedes_children(
            test_database, exec_id, ["deleted-child", "deleted-grandchild"]
        )


@DUAL_BACKEND
def test_an_unrecoverable_root_crash_terminates_the_tree_too(test_database):
    with scheduler_context(db_url=test_database, env=_tick_env()) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="unrecoverable-root")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        _add_child(test_database, exec_id, agent_id, root_id, "crash-child", "running")
        _add_child(
            test_database,
            exec_id,
            agent_id,
            "crash-child",
            "crash-grandchild",
            "running",
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET executor_state = 'crashed', worker_id = NULL, "
                "recovery_attempts = 5, agent_session_id = NULL WHERE id = ?",
                (root_id,),
            )
            conn.commit()

        outcome = _wait_for_execution_outcome(test_database, exec_id)
        assert outcome == "failed", outcome
        _assert_terminal_precedes_children(
            test_database, exec_id, ["crash-child", "crash-grandchild"]
        )


def _root_and_execution(db_url, exec_id, root_id):
    with db_conn(db_url) as conn:
        root = conn.execute(
            "SELECT desired, outcome FROM sessions WHERE id = ?", (root_id,)
        ).fetchone()
        execution = conn.execute(
            "SELECT desired, outcome FROM executions WHERE id = ?", (exec_id,)
        ).fetchone()
    return root, execution


def _assert_never_mixed(db_url, exec_id, root_id):
    root, execution = _root_and_execution(db_url, exec_id, root_id)
    with db_conn(db_url) as conn:
        live = conn.execute(
            "SELECT id FROM sessions WHERE execution_id = ? AND outcome IS NULL "
            "AND desired != 'terminate'",
            (exec_id,),
        ).fetchall()
    if execution[1] is None and execution[0] != "terminate":
        assert root[1] is None, (
            f"live execution over a terminal root: {root} {execution}"
        )
    else:
        assert root[1] is not None, (
            f"terminal execution over a live root: {root} {execution}"
        )
        assert live == [], f"terminal execution over live sessions: {live}"


@DUAL_BACKEND
def test_recovery_during_publication_never_leaves_a_live_root_under_a_terminal(
    test_database,
):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recover-vs-publish")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for index in range(120):
            _add_child(
                test_database,
                exec_id,
                agent_id,
                root_id,
                f"race-child-{index}",
                "running",
            )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'failed', "
                "executor_state = 'crashed', agent_session_id = 'as-1', cwd = '/tmp' "
                "WHERE id = ?",
                (root_id,),
            )
            conn.commit()

        result = {}

        def publish():
            result["response"] = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=60
            )

        thread = threading.Thread(target=publish)
        thread.start()
        time.sleep(0.05)
        recovered = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{root_id}/recover", json={}, timeout=60
        )
        thread.join(timeout=60)
        assert not thread.is_alive()

        _assert_never_mixed(test_database, exec_id, root_id)
        root, execution = _root_and_execution(test_database, exec_id, root_id)
        if recovered.status_code == 200 and root[1] is None:
            assert execution[1] is None, (
                "a terminal row was written over a recovered root"
            )


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_recovery_decides_on_its_in_transaction_read_of_the_execution(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recover-stale-read")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'failed', "
                "executor_state = 'crashed', agent_session_id = 'as-1', cwd = '/tmp' "
                "WHERE id = ?",
                (root_id,),
            )
            conn.commit()

        result = {}

        def recover():
            result["response"] = httpx.post(
                f"{ctx['url']}/api/v1/sessions/{root_id}/recover", json={}, timeout=60
            )

        with db_conn(test_database) as holder:
            holder.execute(
                "SELECT id FROM executions WHERE id = ? FOR UPDATE", (exec_id,)
            )

            thread = threading.Thread(target=recover)
            thread.start()
            time.sleep(2)
            assert thread.is_alive(), "the recover was expected to wait for the lock"

            holder.execute(
                "UPDATE executions SET desired = 'terminate', outcome = 'failed' WHERE id = ?",
                (exec_id,),
            )
            holder.commit()

        thread.join(timeout=60)
        assert not thread.is_alive()
        assert result["response"].status_code == 200, result["response"].text

        root, execution = _root_and_execution(test_database, exec_id, root_id)
        assert root[1] is None, root
        assert execution[1] is None, "the root was revived under a terminal execution"
        _assert_never_mixed(test_database, exec_id, root_id)


@DUAL_BACKEND
def test_recovering_a_child_mid_cascade_never_leaves_it_under_a_terminal(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="recover-child-mid-cascade")
        exec_id, root_id = create_execution_via_api(ctx["url"], agent_id, "task")
        for index in range(120):
            _add_child(
                test_database,
                exec_id,
                agent_id,
                root_id,
                f"mid-child-{index}",
                "running",
            )
        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'terminate', outcome = 'failed', "
                "executor_state = 'crashed', agent_session_id = 'as-1', cwd = '/tmp' "
                "WHERE id = ?",
                ("mid-child-0",),
            )
            conn.commit()

        result = {}

        def publish():
            result["response"] = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=60
            )

        thread = threading.Thread(target=publish)
        thread.start()
        time.sleep(0.05)
        recovered = httpx.post(
            f"{ctx['url']}/api/v1/sessions/mid-child-0/recover",
            json={"message": "carry on"},
            timeout=60,
        )
        thread.join(timeout=60)
        assert not thread.is_alive()

        assert recovered.status_code in (200, 400, 409), recovered.text
        _assert_never_mixed(test_database, exec_id, root_id)
