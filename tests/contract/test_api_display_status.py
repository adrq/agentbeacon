# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import pytest
import requests

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    insert_task,
    set_execution_fields,
    set_session_fields,
    post_worker_sync,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_terminal_completed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
            desired_by="user",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["outcome"] == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_terminal_failed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
            desired_by="system:crash",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_running_is_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_pending_turns_is_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w1",
        )
        insert_task(ctx["db_url"], exec_id, sid, '{"parts":[{"text":"hello"}]}')
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_command_token_is_working(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="unassigned",
            command_token="tok-1",
            command_type="assign",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_stopped(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="stop",
            executor_state="idle",
            worker_id="w1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_stopped_unassigned(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="stop",
            executor_state="unassigned",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_stopped_crashed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="stop",
            executor_state="crashed",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_idle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_unassigned(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "unassigned"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_crashed(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "crashed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_status_in_execution_detail(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/executions/{exec_id}")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) >= 1
        assert sessions[0]["status"] == "working"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_discovery_includes_status(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/executions/{exec_id}/sessions")
        assert resp.status_code == 200
        entries = resp.json()
        assert len(entries) >= 1
        assert entries[0]["status"] == "idle"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_completion_eligible_quiescent_tree(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/executions/{exec_id}")
        assert resp.status_code == 200
        data = resp.json()["execution"]
        assert data["status"] == "awaiting_input"
        assert data["completion_eligible"] is True


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_not_completion_eligible_active_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/executions/{exec_id}")
        assert resp.status_code == 200
        data = resp.json()["execution"]
        assert data["status"] == "working"
        assert data["completion_eligible"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_not_completion_eligible_crashed_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="crashed",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/executions/{exec_id}")
        assert resp.status_code == 200
        data = resp.json()["execution"]
        assert data["status"] == "awaiting_input"
        assert data["completion_eligible"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_completion_eligible_in_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()
        resp = requests.get(f"{ctx['url']}/api/v1/executions")
        assert resp.status_code == 200
        executions = resp.json()
        target = [e for e in executions if e["id"] == exec_id]
        assert len(target) == 1
        assert "completion_eligible" in target[0]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_terminal_not_completion_eligible(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
            desired_by="user",
        )
        set_execution_fields(
            ctx["db_url"],
            exec_id,
            desired="terminate",
            outcome="completed",
        )
        resp = requests.get(f"{ctx['url']}/api/v1/executions/{exec_id}")
        assert resp.status_code == 200
        data = resp.json()["execution"]
        assert data["status"] == "completed"
        assert data["completion_eligible"] is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminate_event_uses_desired_key(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="idle",
            worker_id="w1",
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute("DELETE FROM task_queue WHERE session_id = ?", (sid,))
            conn.commit()

        resp = requests.post(f"{ctx['url']}/api/v1/sessions/{sid}/terminate")
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (sid,),
            ).fetchall()

        terminate_events = [
            json.loads(r[0]) for r in rows if "desired" in json.loads(r[0])
        ]
        assert len(terminate_events) >= 1
        evt = terminate_events[-1]
        assert evt["desired"] == "terminate"
        assert "outcome" in evt
        assert "to_desired" not in evt
        assert "to" not in evt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_event_uses_desired_key(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )

        resp = requests.post(f"{ctx['url']}/api/v1/sessions/{sid}/stop")
        assert resp.status_code == 200

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (sid,),
            ).fetchall()

        stop_events = [
            json.loads(r[0]) for r in rows if json.loads(r[0]).get("desired") == "stop"
        ]
        assert len(stop_events) >= 1
        evt = stop_events[0]
        assert evt["desired"] == "stop"
        assert "desired_by" in evt
        assert "to" not in evt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_running_event_emitted_on_state_transition(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="unassigned",
            worker_id="w1",
        )

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report={"session_id": sid, "executor_state": "running"},
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (sid,),
            ).fetchall()

        running_events = [
            json.loads(r[0])
            for r in rows
            if json.loads(r[0]).get("executor_state") == "running"
        ]
        assert len(running_events) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_idle_event_emitted_on_state_transition(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report={"session_id": sid, "executor_state": "idle"},
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (sid,),
            ).fetchall()

        idle_events = [
            json.loads(r[0])
            for r in rows
            if json.loads(r[0]).get("executor_state") == "idle"
        ]
        assert len(idle_events) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_duplicate_event_on_same_state_report(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report={"session_id": sid, "executor_state": "running"},
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (sid,),
            ).fetchall()

        running_events = [
            json.loads(r[0])
            for r in rows
            if json.loads(r[0]).get("executor_state") == "running"
        ]
        assert len(running_events) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_crash_event_includes_error_fields(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, sid = create_execution_via_api(ctx["url"], agent_id, "test")
        set_session_fields(
            ctx["db_url"],
            sid,
            desired="run",
            executor_state="running",
            worker_id="w1",
        )

        post_worker_sync(
            ctx["url"],
            "w1",
            executor_report={"session_id": sid, "executor_state": "crashed"},
            turn_result={
                "session_id": sid,
                "messages": [],
                "error": "Process died",
                "error_kind": "executor_failed",
            },
        )

        with db_conn(ctx["db_url"]) as conn:
            rows = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change'",
                (sid,),
            ).fetchall()

        crash_events = [
            json.loads(r[0])
            for r in rows
            if json.loads(r[0]).get("executor_state") == "crashed"
        ]
        assert len(crash_events) == 1
        evt = crash_events[0]
        assert evt["error"] == "Process died"
        assert evt["error_kind"] == "executor_failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_list_no_status_filter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, _sid = create_execution_via_api(ctx["url"], agent_id, "test")
        resp = requests.get(f"{ctx['url']}/api/v1/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) >= 1
        for s in sessions:
            assert "status" in s


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_list_no_status_filter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, _sid = create_execution_via_api(ctx["url"], agent_id, "test")
        resp = requests.get(f"{ctx['url']}/api/v1/executions")
        assert resp.status_code == 200
        executions = resp.json()
        assert len(executions) >= 1
        for e in executions:
            assert "status" in e
            assert "completion_eligible" in e
