# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import concurrent.futures
import json

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
        json={"questions": [{"question": "Race?"}], "importance": "blocking"},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(url, session_id):
    resp = httpx.get(f"{url}/api/v1/sessions/{session_id}/events", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _counts(events, batch_id, event_id=None):
    markers = message_answers = delivered = dismissals = 0
    for e in events:
        payload = e.get("payload", {})
        parts = payload.get("parts") or []
        if e["event_type"] == "platform":
            if payload.get("type") == "message_delivered":
                delivered += 1
                continue
            for p in parts:
                d = p.get("data", {})
                if d.get("type") == "question_answer" and d.get("batch_id") == batch_id:
                    markers += 1
                if (
                    d.get("type") == "question_dismiss"
                    and d.get("batch_id") == batch_id
                ):
                    dismissals += 1
        else:
            blob = json.dumps(payload)
            needle = event_id if event_id is not None else batch_id
            if "question_answer" in blob and f'"{needle}"' in blob:
                message_answers += 1
    return {
        "markers": markers,
        "message_answers": message_answers,
        "delivered": delivered,
        "dismissals": dismissals,
    }


def _answer_req(url, session_id, event_id, text):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": [
                {"text": text},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ]
        },
        timeout=30,
    )


def _dismiss_req(url, event_id):
    return httpx.post(f"{url}/api/v1/escalate/{event_id}/dismiss", timeout=30)


def _decision(url, event_id):
    resp = httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_concurrent_double_answer_exact_oracle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_answer_req, ctx["url"], session_id, event_id, "answer-A")
            f2 = pool.submit(_answer_req, ctx["url"], session_id, event_id, "answer-B")
            r1, r2 = f1.result(), f2.result()

        assert sorted([r1.status_code, r2.status_code]) == [200, 409]
        loser = r1 if r1.status_code == 409 else r2
        assert loser.json()["code"] == "decision.already_resolved"
        winner_text = "answer-A" if r1.status_code == 200 else "answer-B"

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        assert c["markers"] == 1
        assert c["message_answers"] == 1
        assert c["delivered"] == 1
        found = [_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] == winner_text


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_concurrent_answer_and_dismiss_exact_oracle(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]
        base_depth = _queue_depth(test_database, session_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(
                _answer_req, ctx["url"], session_id, event_id, "the answer"
            )
            fd = pool.submit(_dismiss_req, ctx["url"], event_id)
            ra, rd = fa.result(), fd.result()

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        depth = _queue_depth(test_database, session_id)
        if ra.status_code == 200:
            assert rd.status_code == 409
            assert rd.json()["code"] == "decision.already_resolved"
            assert rd.json()["resolution"]["kind"] == "answered"
            assert c == {
                "markers": 1,
                "message_answers": 1,
                "delivered": 1,
                "dismissals": 0,
            }
            assert depth == base_depth + 1
            expected_status, expected_answer = "answered", "the answer"
        else:
            assert ra.status_code == 409
            assert ra.json()["code"] == "decision.already_resolved"
            assert rd.status_code == 200 and rd.json()["status"] == "dismissed"
            assert c == {
                "markers": 0,
                "message_answers": 0,
                "delivered": 0,
                "dismissals": 1,
            }
            assert depth == base_depth
            expected_status, expected_answer = "dismissed", None

        found = [_decision(ctx["url"], event_id)]
        assert found[0]["status"] == expected_status
        assert found[0]["answer"] == expected_answer


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_sequential_answer_then_dismiss_exact_oracle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]

        ra = _answer_req(ctx["url"], session_id, event_id, "chosen")
        assert ra.status_code == 200
        rd = _dismiss_req(ctx["url"], event_id)
        assert rd.status_code == 409
        assert rd.json()["code"] == "decision.already_resolved"

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        assert c["markers"] == 1
        assert c["dismissals"] == 0
        assert c["delivered"] == 1


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_sequential_dismiss_then_answer_exact_oracle(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]

        rd = _dismiss_req(ctx["url"], event_id)
        assert rd.status_code == 200 and rd.json()["status"] == "dismissed"
        ra = _answer_req(ctx["url"], session_id, event_id, "too late")
        assert ra.status_code == 409
        assert ra.json()["code"] == "decision.already_resolved"

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        assert c["dismissals"] == 1
        assert c["markers"] == 0
        assert c["message_answers"] == 0


def _queue_depth(db_url, session_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE session_id = ?", (session_id,)
        ).fetchone()[0]


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_and_dismiss_queue_depth_oracle(test_database):
    with scheduler_context(
        db_url=test_database, env={"AGENTBEACON_MAX_WORKERS": "0"}
    ) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec, session_id = create_execution_via_api(ctx["url"], agent_id)

        b1 = _escalate(ctx["url"], session_id)["event_id"]
        base = _queue_depth(test_database, session_id)
        assert _answer_req(ctx["url"], session_id, b1, "a").status_code == 200
        assert _queue_depth(test_database, session_id) == base + 1
        rd = _dismiss_req(ctx["url"], b1)
        assert rd.status_code == 409
        assert rd.json()["code"] == "decision.already_resolved"
        assert _queue_depth(test_database, session_id) == base + 1

        _exec2, session2 = create_execution_via_api(ctx["url"], agent_id)
        b2 = _escalate(ctx["url"], session2)["event_id"]
        base2 = _queue_depth(test_database, session2)
        assert _dismiss_req(ctx["url"], b2).status_code == 200
        assert _queue_depth(test_database, session2) == base2
        assert _answer_req(ctx["url"], session2, b2, "late").status_code == 409
        assert _queue_depth(test_database, session2) == base2


def _mark_execution_terminal(db_url, exec_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
        )
        conn.commit()


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_first_stays_answered_after_terminate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        event_id = decision["event_id"]

        assert _answer_req(ctx["url"], session_id, event_id, "kept").status_code == 200
        _mark_execution_terminal(test_database, exec_id)

        found = [_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] == "kept"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_terminate_first_rejects_answer_and_expires(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]

        _mark_execution_terminal(test_database, exec_id)
        ra = _answer_req(ctx["url"], session_id, event_id, "too late")
        assert ra.status_code == 409
        assert ra.json()["code"] in ("decision.expired", "execution.terminated")

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        assert c["markers"] == 0 and c["message_answers"] == 0
        found = [_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "expired"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_dismiss_first_stays_dismissed_after_terminate(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]

        rd = _dismiss_req(ctx["url"], event_id)
        assert rd.status_code == 200 and rd.json()["status"] == "dismissed"
        _mark_execution_terminal(test_database, exec_id)

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        assert c["dismissals"] == 1 and c["markers"] == 0
        found = [_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "dismissed"
        assert found[0]["answer"] is None


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_terminate_first_rejects_dismiss_and_expires(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        decision = _escalate(ctx["url"], session_id)
        batch_id = decision["batch_id"]
        event_id = decision["event_id"]

        _mark_execution_terminal(test_database, exec_id)
        rd = _dismiss_req(ctx["url"], event_id)
        assert rd.status_code == 409
        assert rd.json()["code"] == "decision.expired"

        c = _counts(_events(ctx["url"], session_id), batch_id, event_id)
        assert c["dismissals"] == 0 and c["markers"] == 0
        found = [_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "expired"
