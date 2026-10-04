# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

BACKENDS = ["sqlite", "postgres"]


def _escalate(url, session_id):
    resp = httpx.post(
        f"{url}/api/v1/escalate",
        json={"questions": [{"question": "Q?"}], "importance": "blocking"},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["event_id"]


def _answer(url, session_id, event_id):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": [
                {"text": "answer"},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ]
        },
        timeout=10,
    )


def _event_count(db_url, exec_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE execution_id = ?", (exec_id,)
        ).fetchone()[0]


def _queue_count(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]


def _decision_status(url, exec_id, event_id):
    resp = httpx.get(
        f"{url}/api/v1/decisions", params={"execution_id": exec_id}, timeout=10
    )
    found = [d for d in resp.json()["decisions"] if d["event_id"] == event_id]
    return found[0]["status"] if found else None


@pytest.mark.parametrize("site", ["marker_insert", "message_delivered_insert"])
@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_failpoint_rolls_back_answer(test_database, site):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_TEST_FAIL_AT": site}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        event_id = _escalate(ctx["url"], session_id)

        before = _event_count(test_database, exec_id)
        queue_before = _queue_count(test_database, session_id)

        resp = _answer(ctx["url"], session_id, event_id)
        assert resp.status_code == 500
        assert f"test failpoint at {site}" in resp.json()["error"]

        assert _event_count(test_database, exec_id) == before
        assert _queue_count(test_database, session_id) == queue_before
        assert _decision_status(ctx["url"], exec_id, event_id) == "pending"


@pytest.mark.parametrize("site", ["marker_insert", "message_delivered_insert"])
@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_failpoint_reverts_auto_resume(test_database, site):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_TEST_FAIL_AT": site}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        event_id = _escalate(ctx["url"], session_id)

        with db_conn(test_database) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'stop' WHERE id = ?", (session_id,)
            )
            conn.commit()

        resp = _answer(ctx["url"], session_id, event_id)
        assert resp.status_code == 500
        assert f"test failpoint at {site}" in resp.json()["error"]

        with db_conn(test_database) as conn:
            desired = conn.execute(
                "SELECT desired FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
        assert desired == "stop"
