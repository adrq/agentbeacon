import json
import threading
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def _worker_sync(url, payload=None, timeout=10):
    if payload is None:
        payload = {}
    resp = httpx.post(f"{url}/api/worker/sync", json=payload, timeout=timeout)
    assert resp.status_code == 200, (
        f"worker sync failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _insert_task(db_url, execution_id, session_id, text):
    with db_conn(db_url) as conn:
        payload = json.dumps(
            {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}}
        )
        conn.execute(
            "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
            (execution_id, session_id, payload),
        )
        conn.commit()


def _stop_session(url, session_id):
    return httpx.post(f"{url}/api/sessions/{session_id}/stop", timeout=5)


def _post_user_message(url, session_id, text):
    return httpx.post(
        f"{url}/api/sessions/{session_id}/message",
        json={"parts": [{"kind": "text", "text": text}]},
        timeout=5,
    )


def _post_worker_message(
    url, execution_id, session_id, text, msg_seq=1, ephemeral=False
):
    resp = httpx.post(
        f"{url}/api/worker/events",
        json={
            "executionId": execution_id,
            "sessionId": session_id,
            "msgSeq": msg_seq,
            "ephemeral": ephemeral,
            "payload": {
                "role": "assistant",
                "parts": [{"kind": "text", "text": text}],
            },
        },
        timeout=5,
    )
    assert resp.status_code in (200, 201), (
        f"worker event failed: {resp.status_code} {resp.text}"
    )


def _ack_stop_without_active_turn(url, session_id):
    return _worker_sync(
        url,
        {
            "sessionState": {
                "sessionId": session_id,
                "status": "running",
            },
            "sessionResult": {
                "sessionId": session_id,
                "errorKind": "stopped_by_user",
            },
        },
    )


def _get_statuses(db_url, execution_id, session_id):
    with db_conn(db_url) as conn:
        session_row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        execution_row = conn.execute(
            "SELECT status FROM executions WHERE id = ?",
            (execution_id,),
        ).fetchone()
        return session_row[0], execution_row[0]


def _queue_texts_for_session(db_url, session_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    texts = []
    for row in rows:
        payload = json.loads(row[0])
        texts.append(payload["message"]["parts"][0]["text"])
    return texts


def _queue_count_for_session(db_url, session_id):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0]


def _setup_parent_child(ctx):
    agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
    exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "lead task")
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
            (exec_id, agent_id),
        )
        conn.commit()

    data = _worker_sync(ctx["url"])
    assert data["type"] == "session_assigned"
    assert data["sessionId"] == lead_id

    result = mcp_tools_call(
        ctx["url"],
        lead_id,
        "delegate",
        {"agent": "stop-agent", "prompt": "child task"},
    )
    child_id = json.loads(result["content"][0]["text"])["session_id"]

    data = _worker_sync(ctx["url"])
    assert data["type"] == "session_assigned"
    assert data["sessionId"] == child_id

    return exec_id, lead_id, child_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_flushes_current_queue_and_returns_input_required(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id

        _insert_task(ctx["db_url"], exec_id, session_id, "queued before stop")

        stop_resp = _stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["tasks_flushed"] == 1

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        ack = _ack_stop_without_active_turn(ctx["url"], session_id)
        assert ack["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "input-required"
        assert execution_status == "input-required"
        assert _queue_texts_for_session(ctx["db_url"], session_id) == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_completion_after_parent_stop_is_valid_new_work(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        _insert_task(ctx["db_url"], exec_id, lead_id, "queued before stop")

        stop_resp = _stop_session(ctx["url"], lead_id)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["tasks_flushed"] == 1

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": lead_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        ack = _ack_stop_without_active_turn(ctx["url"], lead_id)
        assert ack["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, lead_id
        )
        assert session_status == "input-required"
        assert execution_status == "input-required"

        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": child_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "child output"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, lead_id
        )
        assert session_status == "working"
        assert execution_status == "working"

        queued_texts = _queue_texts_for_session(ctx["db_url"], lead_id)
        assert queued_texts == [
            f"[turn complete from stop-agent \u00b7 session {child_id}]\n\nchild output"
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_replayed_stop_ack_does_not_override_new_work(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id

        stop_resp = _stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        ack = _ack_stop_without_active_turn(ctx["url"], session_id)
        assert ack["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "input-required"
        assert execution_status == "input-required"

        message_resp = _post_user_message(ctx["url"], session_id, "new work")
        assert message_resp.status_code == 200

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "working"
        assert execution_status == "working"

        replay = _ack_stop_without_active_turn(ctx["url"], session_id)
        assert replay["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "working"
        assert execution_status == "working"
        assert _queue_texts_for_session(ctx["db_url"], session_id) == ["new work"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_ack_with_final_turn_output_is_not_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id

        stop_resp = _stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        ack = _worker_sync(
            ctx["url"],
            {
                "sessionState": {"sessionId": session_id, "status": "running"},
                "sessionResult": {
                    "sessionId": session_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "partial output"}],
                            },
                        }
                    ],
                    "errorKind": "stopped_by_user",
                },
            },
        )
        assert ack["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "input-required"
        assert execution_status == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_ack_after_persisted_midturn_output_is_not_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id

        stop_resp = _stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        _post_worker_message(ctx["url"], exec_id, session_id, "partial output")

        ack = _ack_stop_without_active_turn(ctx["url"], session_id)
        assert ack["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "input-required"
        assert execution_status == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_survives_user_message_queued_while_turn_is_running(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id

        stop_resp = _stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200

        message_resp = _post_user_message(ctx["url"], session_id, "queued after stop")
        assert message_resp.status_code == 200

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": session_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        assert _queue_texts_for_session(ctx["db_url"], session_id) == [
            "queued after stop"
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_fetch_task_discards_task_when_stop_arrives_after_pop(test_database):
    with scheduler_context(
        db_url=test_database,
        env={"AGENTBEACON_TEST_FETCH_TASK_POST_POP_DELAY_MS": "200"},
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="stop-agent")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        data = _worker_sync(ctx["url"])
        assert data["type"] == "session_assigned"
        assert data["sessionId"] == session_id
        _insert_task(ctx["db_url"], exec_id, session_id, "escaped task")

        result_holder = [None]

        def do_fetch():
            result_holder[0] = _worker_sync(
                ctx["url"],
                {
                    "sessionState": {
                        "sessionId": session_id,
                        "status": "fetch_task",
                    }
                },
            )

        t = threading.Thread(target=do_fetch)
        t.start()

        deadline = time.time() + 2.0
        while time.time() < deadline:
            if _queue_count_for_session(ctx["db_url"], session_id) == 0:
                break
            time.sleep(0.01)
        else:
            assert False, "fetch_task did not pop the queued task before stop"

        stop_resp = _stop_session(ctx["url"], session_id)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["tasks_flushed"] == 0

        t.join(timeout=5)
        assert not t.is_alive(), "fetch_task should have returned after stop"

        data = result_holder[0]
        assert data == {"type": "command", "command": "stop_turn"}
        assert _queue_texts_for_session(ctx["db_url"], session_id) == []

        ack = _ack_stop_without_active_turn(ctx["url"], session_id)
        assert ack["type"] == "no_action"

        session_status, execution_status = _get_statuses(
            ctx["db_url"], exec_id, session_id
        )
        assert session_status == "input-required"
        assert execution_status == "input-required"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_stop_survives_child_completion_queued_while_parent_turn_is_running(
    test_database,
):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        stop_resp = _stop_session(ctx["url"], lead_id)
        assert stop_resp.status_code == 200

        _worker_sync(
            ctx["url"],
            {
                "sessionResult": {
                    "sessionId": child_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "child output"}],
                            },
                        }
                    ],
                    "hasPendingTurn": False,
                }
            },
        )

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": lead_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        queued_texts = _queue_texts_for_session(ctx["db_url"], lead_id)
        assert queued_texts == [
            f"[turn complete from stop-agent \u00b7 session {child_id}]\n\nchild output"
        ]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_stop_suppresses_parent_notification_even_with_turn_output(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        exec_id, lead_id, child_id = _setup_parent_child(ctx)

        stop_resp = _stop_session(ctx["url"], child_id)
        assert stop_resp.status_code == 200

        command = _worker_sync(
            ctx["url"],
            {"sessionState": {"sessionId": child_id, "status": "waiting_for_event"}},
        )
        assert command == {"type": "command", "command": "stop_turn"}

        ack = _worker_sync(
            ctx["url"],
            {
                "sessionState": {"sessionId": child_id, "status": "running"},
                "sessionResult": {
                    "sessionId": child_id,
                    "turnMessages": [
                        {
                            "msgSeq": 1,
                            "payload": {
                                "role": "assistant",
                                "parts": [{"kind": "text", "text": "child output"}],
                            },
                        }
                    ],
                    "errorKind": "stopped_by_user",
                },
            },
        )
        assert ack["type"] == "no_action"

        child_status, execution_status = _get_statuses(ctx["db_url"], exec_id, child_id)
        assert child_status == "input-required"
        assert execution_status in ("working", "input-required")

        assert _queue_texts_for_session(ctx["db_url"], lead_id) == []
