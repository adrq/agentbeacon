# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import uuid
from datetime import datetime

import httpx
import pytest

from tests.testhelpers import (
    assert_event_exists,
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)
from tests.mock_agent_helpers import (
    assert_session_state,
    assert_no_command,
    create_child_session_raw,
    get_session_row,
    get_task_queue_count,
    get_task_queue_payload,
    set_session_fields,
    set_execution_fields,
)


def _continue_session(url, session_id, prompt="continue working", timeout=5):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/continue",
        json={"parts": [{"text": prompt}]},
        timeout=timeout,
    )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_failed_child_creates_new_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "DELETE FROM task_queue WHERE session_id = ?",
                (root_sid,),
            )
            conn.commit()

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 201

        data = resp.json()
        new_sid = data["session_id"]
        assert new_sid != child_sid

        assert_session_state(
            ctx["db_url"],
            new_sid,
            desired="run",
            executor_state="unassigned",
            outcome=None,
        )

        new_row = get_session_row(ctx["db_url"], new_sid)
        assert new_row["recovery_attempts"] == 0
        assert new_row["parent_session_id"] == root_sid
        assert new_row["agent_id"] == agent_id
        assert new_row["execution_id"] == exec_id

        parent_tasks = get_task_queue_count(ctx["db_url"], root_sid)
        assert parent_tasks == 1

        assert_event_exists(ctx["db_url"], root_sid, "platform", "child_continued")


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_transfers_sdk_context_resumable_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], agent_type="claude_sdk")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(ctx["db_url"], child_sid, agent_session_id="sdk-sess-abc")

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 201

        new_sid = resp.json()["session_id"]

        new_row = get_session_row(ctx["db_url"], new_sid)
        assert new_row["agent_session_id"] == "sdk-sess-abc"

        old_row = get_session_row(ctx["db_url"], child_sid)
        assert old_row["agent_session_id"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_non_resumable_agent_no_sdk_transfer(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], agent_type="acp")
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        set_session_fields(
            ctx["db_url"], child_sid, agent_session_id="non-resumable-sess"
        )

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 201

        new_sid = resp.json()["session_id"]
        new_row = get_session_row(ctx["db_url"], new_sid)
        assert new_row["agent_session_id"] is None


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_non_terminal_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="run",
            executor_state="idle",
            outcome=None,
            worker_id="w-idle",
        )

        with db_conn(ctx["db_url"]) as conn:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE execution_id = ?",
                (exec_id,),
            ).fetchone()[0]

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409

        with db_conn(ctx["db_url"]) as conn:
            count_after = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE execution_id = ?",
                (exec_id,),
            ).fetchone()[0]
        assert count_after == count_before, "rejection must not create a new session"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_pending_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="canceled",
        )
        cmd_token = str(uuid.uuid4())
        set_session_fields(
            ctx["db_url"],
            child_sid,
            command_token=cmd_token,
            command_type="cancel",
            command_has_payload=False,
            command_at=datetime.utcnow().isoformat(),
        )

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409

        row = get_session_row(ctx["db_url"], child_sid)
        assert row["command_token"] == cmd_token, "rejection must not clear command"
        assert row["outcome"] == "canceled", "rejection must not change outcome"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_worker_attached(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(ctx["db_url"], child_sid, worker_id="stale-worker")

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_dead_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )
        set_execution_fields(
            ctx["db_url"], exec_id, outcome="completed", desired="terminate"
        )

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_dead_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            outcome="completed",
        )

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_deleted_agent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE agents SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (agent_id,),
            )
            conn.commit()

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_missing_prompt(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{child_sid}/continue",
            json={},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_new_session_enters_normal_lifecycle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        resp = _continue_session(ctx["url"], child_sid, prompt="do more work")
        assert resp.status_code == 201

        new_sid = resp.json()["session_id"]
        assert_session_state(
            ctx["db_url"],
            new_sid,
            desired="run",
            executor_state="unassigned",
            outcome=None,
        )
        assert_no_command(ctx["db_url"], new_sid)
        assert get_task_queue_count(ctx["db_url"], new_sid) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continued_from_linkage_set(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 201

        new_sid = resp.json()["session_id"]
        new_row = get_session_row(ctx["db_url"], new_sid)
        assert new_row["continued_from_session_id"] == child_sid


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_root_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        set_session_fields(
            ctx["db_url"],
            root_sid,
            desired="terminate",
            outcome="completed",
            executor_state="idle",
        )

        resp = _continue_session(ctx["url"], root_sid)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_rejects_execution_terminating(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")

        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_execution_fields(ctx["db_url"], exec_id, desired="terminate")

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_auto_resumes_stopped_parent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test")
        child_id = create_child_session_raw(
            ctx["db_url"],
            lead_id,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )
        set_session_fields(
            ctx["db_url"], lead_id, desired="stop", executor_state="idle"
        )
        resp = _continue_session(ctx["url"], child_id, "retry prompt")
        assert resp.status_code == 201

        parent_row = get_session_row(ctx["db_url"], lead_id)
        assert parent_row["desired"] == "run", "stopped parent should be auto-resumed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_old_session_stays_terminal(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")
        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        resp = _continue_session(ctx["url"], child_sid)
        assert resp.status_code == 201

        old_row = get_session_row(ctx["db_url"], child_sid)
        assert old_row["desired"] == "terminate"
        assert old_row["outcome"] == "failed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_from_canceled_child(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")
        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="canceled",
        )

        resp = _continue_session(ctx["url"], child_sid, "resume canceled work")
        assert resp.status_code == 201
        new_sid = resp.json()["session_id"]
        assert_session_state(
            ctx["db_url"],
            new_sid,
            desired="run",
            executor_state="unassigned",
            outcome=None,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_from_completed_child(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")
        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="idle",
            outcome="completed",
        )

        resp = _continue_session(ctx["url"], child_sid, "add error handling too")
        assert resp.status_code == 201
        new_sid = resp.json()["session_id"]
        assert_session_state(
            ctx["db_url"],
            new_sid,
            desired="run",
            executor_state="unassigned",
            outcome=None,
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_continue_enqueues_prompt_in_new_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, root_sid = create_execution_via_api(ctx["url"], agent_id, "start")
        child_sid = create_child_session_raw(
            ctx["db_url"],
            root_sid,
            exec_id,
            agent_id,
            desired="terminate",
            executor_state="crashed",
            outcome="failed",
        )

        resp = _continue_session(ctx["url"], child_sid, "fix the database migration")
        assert resp.status_code == 201
        new_sid = resp.json()["session_id"]

        payload = get_task_queue_payload(ctx["db_url"], new_sid)
        assert payload is not None
        text = payload["message"]["parts"][0]["text"]
        assert "fix the database migration" in text
