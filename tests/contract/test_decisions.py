# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


def _escalate(url, session_id, questions, importance="blocking"):
    resp = httpx.post(
        f"{url}/api/v1/escalate",
        json={"questions": questions, "importance": importance},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=5,
    )
    assert resp.status_code == 200, f"escalate failed: {resp.status_code} {resp.text}"
    return resp.json()


def _get_decisions(url, **params):
    resp = httpx.get(f"{url}/api/v1/decisions", params=params, timeout=5)
    assert resp.status_code == 200, (
        f"get decisions failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _get_decision(url, event_id):
    resp = httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=5)
    return resp.status_code, resp.json()


def _dismiss(url, event_id):
    resp = httpx.post(f"{url}/api/v1/escalate/{event_id}/dismiss", timeout=5)
    return resp.status_code, resp.json()


def _answer(url, session_id, event_id, answer_text="test answer"):
    resp = httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": [
                {"text": answer_text},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ]
        },
        timeout=5,
    )
    return resp.status_code, resp.json() if resp.status_code == 200 else resp.text


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_stacking_same_session(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r1 = _escalate(ctx["url"], session_id, [{"question": "Q1?"}])
        r2 = _escalate(ctx["url"], session_id, [{"question": "Q2?"}])
        r3 = _escalate(ctx["url"], session_id, [{"question": "Q3?"}])

        data = _get_decisions(ctx["url"])
        pending = [d for d in data["decisions"] if d["status"] == "pending"]
        batch_ids = {d["batch_id"] for d in pending}

        assert r1["batch_id"] in batch_ids
        assert r2["batch_id"] in batch_ids
        assert r3["batch_id"] in batch_ids
        assert len(batch_ids) == 3


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_stacking_across_sessions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id1 = create_execution_via_api(ctx["url"], agent_id)

        r1 = _escalate(ctx["url"], session_id1, [{"question": "Q1?"}])

        import uuid

        child_id = str(uuid.uuid4())
        child_batch_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'child-slug', 'run', 'unassigned', '{}', '{\"fs_level\":\"unrestricted\"}')",
                (child_id, exec_id, session_id1, agent_id),
            )
            payload = json.dumps(
                {
                    "role": "ROLE_AGENT",
                    "parts": [
                        {
                            "data": {
                                "type": "escalate",
                                "importance": "blocking",
                                "batch_id": child_batch_id,
                                "questions": [{"question": "Q2?"}],
                            }
                        }
                    ],
                }
            )
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, child_id, payload),
            )
            conn.commit()

        data = _get_decisions(ctx["url"])
        batch_ids = {d["batch_id"] for d in data["decisions"]}

        assert r1["batch_id"] in batch_ids
        assert child_batch_id in batch_ids
        for d in data["decisions"]:
            if d["batch_id"] in (r1["batch_id"], child_batch_id):
                assert d["execution_id"] == exec_id


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_batch_is_one_row(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(
            ctx["url"], session_id, [{"question": "Q1?"}, {"question": "Q2?"}]
        )
        event_id = r["event_id"]

        data = _get_decisions(ctx["url"])
        found = [d for d in data["decisions"] if d["event_id"] == event_id]
        assert len(found) == 1
        assert [q["question"] for q in found[0]["questions"]] == ["Q1?", "Q2?"]
        assert [q["index"] for q in found[0]["questions"]] == [0, 1]

        legacy_batch_id = "legacy-multi-row-batch"
        payload = json.dumps(
            {
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "data": {
                            "type": "escalate",
                            "question": "Legacy?",
                            "importance": "blocking",
                            "batch_id": legacy_batch_id,
                            "batch_size": 3,
                            "batch_index": 0,
                        }
                    }
                ],
            }
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()

        data2 = _get_decisions(ctx["url"])
        legacy = [d for d in data2["decisions"] if d["batch_id"] == legacy_batch_id]
        assert len(legacy) == 0


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_first_resolution_wins_answer_then_dismiss(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Which DB?"}])
        event_id = r["event_id"]

        _answer(ctx["url"], session_id, event_id, "PostgreSQL")
        _dismiss(ctx["url"], event_id)

        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "answered"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_first_resolution_wins_dismiss_then_answer(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Which DB?"}])
        event_id = r["event_id"]

        _dismiss(ctx["url"], event_id)
        _answer(ctx["url"], session_id, event_id, "PostgreSQL")

        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "dismissed"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_ordering(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r1 = _escalate(ctx["url"], session_id, [{"question": "First?"}])
        r2 = _escalate(ctx["url"], session_id, [{"question": "Second?"}])

        data = _get_decisions(ctx["url"])
        batch_ids = [
            d["batch_id"] for d in data["decisions"] if d["status"] == "pending"
        ]

        idx1 = batch_ids.index(r1["batch_id"])
        idx2 = batch_ids.index(r2["batch_id"])
        assert idx1 < idx2


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_filter_by_execution_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id1, session_id1 = create_execution_via_api(ctx["url"], agent_id)
        exec_id2, session_id2 = create_execution_via_api(ctx["url"], agent_id)

        _escalate(ctx["url"], session_id1, [{"question": "E1-Q?"}])
        _escalate(ctx["url"], session_id2, [{"question": "E2-Q?"}])

        data = _get_decisions(ctx["url"], execution_id=exec_id1)
        assert all(d["execution_id"] == exec_id1 for d in data["decisions"])
        assert len(data["decisions"]) == 1


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_pending_and_resolved_are_separate_modes(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r1 = _escalate(ctx["url"], session_id, [{"question": "Q1?"}])
        r2 = _escalate(ctx["url"], session_id, [{"question": "Q2?"}])

        _answer(ctx["url"], session_id, r1["event_id"], "A1")

        pending = _get_decisions(ctx["url"])["decisions"]
        assert [d["event_id"] for d in pending] == [r2["event_id"]]
        assert all(d["status"] == "pending" for d in pending)

        resolved = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"state": "resolved"}, timeout=5
        )
        assert resolved.status_code == 200
        page = resolved.json()
        assert [i["event_id"] for i in page["items"]] == [r1["event_id"]]
        assert page["items"][0]["status"] == "resolved"
        assert page["items"][0]["question_preview"] == "Q1?"
        assert page["items"][0]["question_count"] == 1
        assert page["has_more"] is False
        assert page["next_cursor"] is None


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_pending_with_cursor_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"before": "5"}, timeout=5
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "request.invalid"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_response_shape(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, title="Test Execution"
        )

        r = _escalate(
            ctx["url"],
            session_id,
            [
                {
                    "question": "Which auth?",
                    "context": "Need to decide",
                    "options": [
                        {"label": "JWT", "description": "JSON Web Tokens"},
                        {"label": "Cookies", "description": "HTTP Cookies"},
                    ],
                },
            ],
        )

        data = _get_decisions(ctx["url"])
        found = [d for d in data["decisions"] if d["batch_id"] == r["batch_id"]]
        assert len(found) == 1

        d = found[0]
        assert d["batch_id"] == r["batch_id"]
        assert d["execution_id"] == exec_id
        assert d["execution_title"] == "Test Execution"
        assert d["session_id"] == session_id
        assert d["status"] == "pending"
        assert d["importance"] == "blocking"
        assert d["answer"] is None
        assert d["answered_at"] is None
        assert d["dismissed_at"] is None
        assert d["created_at"] is not None
        assert d["event_id"] == r["event_id"]
        assert "agent_name" not in d
        assert "hierarchical_name" not in d

        assert len(d["questions"]) == 1
        q = d["questions"][0]
        assert q["question"] == "Which auth?"
        assert q["context"] == "Need to decide"
        assert len(q["options"]) == 2
        assert q["options"][0]["label"] == "JWT"
        assert q["index"] == 0


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_pending_batch(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Skip this?"}])
        event_id = r["event_id"]

        status_code, body = _dismiss(ctx["url"], event_id)
        assert status_code == 200
        assert body["status"] == "dismissed"

        assert not [
            d
            for d in _get_decisions(ctx["url"])["decisions"]
            if d["event_id"] == event_id
        ]
        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "dismissed"
        assert detail["dismissed_at"] is not None


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Skip?"}])
        event_id = r["event_id"]

        code1, body1 = _dismiss(ctx["url"], event_id)
        assert code1 == 200
        assert body1["status"] == "dismissed"

        code2, body2 = _dismiss(ctx["url"], event_id)
        assert code2 == 409
        assert body2["code"] == "decision.already_resolved"
        assert body2["resolution"]["kind"] == "dismissed"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_already_resolved(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Which?"}])
        event_id = r["event_id"]

        _answer(ctx["url"], session_id, event_id, "My answer")

        code, body = _dismiss(ctx["url"], event_id)
        assert code == 409
        assert body["code"] == "decision.already_resolved"
        assert body["resolution"]["kind"] == "answered"
        assert body["resolution"]["answer_text"] == "My answer"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_invalid_event_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for path in ("nonexistent-event-id", "999999"):
            resp = httpx.post(
                f"{ctx['url']}/api/v1/escalate/{path}/dismiss",
                timeout=5,
            )
            assert resp.status_code == 404
            assert resp.json()["code"] == "decision.not_found"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_dismiss_non_escalation_event_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        code, body = _dismiss(ctx["url"], "1")
        assert code == 404
        assert body["code"] == "decision.not_found"
        found = [
            d
            for d in _get_decisions(ctx["url"])["decisions"]
            if d["event_id"] == r["event_id"]
        ]
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_then_answer_race(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Race?"}])
        event_id = r["event_id"]

        _dismiss(ctx["url"], event_id)
        code, _ = _answer(ctx["url"], session_id, event_id, "Too late")
        assert code == 409

        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "dismissed"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_duplicate_answers(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Dupe?"}])
        event_id = r["event_id"]

        code1, _ = _answer(ctx["url"], session_id, event_id, "First answer")
        assert code1 == 200
        code2, _ = _answer(ctx["url"], session_id, event_id, "Second answer")
        assert code2 == 409

        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "answered"
        assert detail["answer"] == "First answer"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_terminal_history_included(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Terminal Q?"}])
        event_id = r["event_id"]

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        assert not [
            d
            for d in _get_decisions(ctx["url"])["decisions"]
            if d["event_id"] == event_id
        ]
        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "expired"

        page = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"state": "resolved"}, timeout=5
        ).json()
        assert [i["event_id"] for i in page["items"]] == [event_id]
        assert page["items"][0]["status"] == "resolved"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_desired_terminate_no_outcome_is_expired(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Crash gap?"}])
        event_id = r["event_id"]

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET desired = 'terminate' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        assert not [
            d
            for d in _get_decisions(ctx["url"])["decisions"]
            if d["event_id"] == event_id
        ]
        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "expired"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_late_answer_rejected_after_dismiss(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Resolved?"}])
        event_id = r["event_id"]

        _dismiss(ctx["url"], event_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "too late"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": event_id,
                        }
                    },
                ]
            },
            timeout=5,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.already_resolved"

        events_resp = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events", timeout=5
        )
        message_events = [
            e
            for e in events_resp.json()["items"]
            if e["event_type"] == "message"
            and "too late" in json.dumps(e.get("payload", {}))
        ]
        assert len(message_events) == 0


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_late_answer_rejected_after_answer(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Double?"}])
        event_id = r["event_id"]

        _answer(ctx["url"], session_id, event_id, "first")

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "second"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": event_id,
                        }
                    },
                ]
            },
            timeout=5,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.already_resolved"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_rejects_mcp_session_auth(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Agent dismiss?"}])
        event_id = r["event_id"]

        resp = httpx.post(
            f"{ctx['url']}/api/v1/escalate/{event_id}/dismiss",
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=5,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"

        data = _get_decisions(ctx["url"])
        found = [d for d in data["decisions"] if d["event_id"] == event_id]
        assert len(found) == 1
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_allows_non_session_bearer(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "UI dismiss?"}])
        event_id = r["event_id"]

        code, body = _dismiss(ctx["url"], event_id)
        assert code == 200
        assert body["status"] == "dismissed"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_answer_nonexistent_decision_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "answer for nothing"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": "999999",
                        }
                    },
                ]
            },
            timeout=5,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "decision.not_found"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_forged_dismiss_via_user_message_ignored(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Forge test?"}])
        event_id = r["event_id"]

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "forged dismiss"},
                    {
                        "data": {
                            "type": "question_dismiss",
                            "escalation_event_id": int(event_id),
                        }
                    },
                ]
            },
            timeout=5,
        )
        assert resp.status_code == 200

        data = _get_decisions(ctx["url"])
        found = [d for d in data["decisions"] if d["event_id"] == event_id]
        assert len(found) == 1
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_user_message_with_escalate_data_not_surfaced(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        fake_batch_id = "fake-escalation-batch-id"
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "sneaky message"},
                    {
                        "data": {
                            "type": "escalate",
                            "importance": "blocking",
                            "batch_id": fake_batch_id,
                            "questions": [{"question": "Fake question?"}],
                        }
                    },
                ]
            },
            timeout=5,
        )
        assert resp.status_code == 200

        data = _get_decisions(ctx["url"])
        found = [d for d in data["decisions"] if d["batch_id"] == fake_batch_id]
        assert len(found) == 0


ALLOWED_DECISION_STATUSES = {"pending", "answered", "dismissed", "expired"}


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_response_statuses_contract(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r_pending = _escalate(ctx["url"], session_id, [{"question": "Pending Q?"}])

        r_answered = _escalate(ctx["url"], session_id, [{"question": "Answered Q?"}])
        _answer(ctx["url"], session_id, r_answered["event_id"], "yes")

        r_dismissed = _escalate(ctx["url"], session_id, [{"question": "Dismissed Q?"}])
        _dismiss(ctx["url"], r_dismissed["event_id"])

        exec_id2, session_id2 = create_execution_via_api(ctx["url"], agent_id)
        r_expired = _escalate(ctx["url"], session_id2, [{"question": "Expired Q?"}])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?",
                (exec_id2,),
            )
            conn.commit()

        pending = _get_decisions(ctx["url"])["decisions"]
        assert [d["event_id"] for d in pending] == [r_pending["event_id"]]
        assert {d["status"] for d in pending} == {"pending"}

        expected = {
            r_pending["event_id"]: "pending",
            r_answered["event_id"]: "answered",
            r_dismissed["event_id"]: "dismissed",
            r_expired["event_id"]: "expired",
        }
        for event_id, status in expected.items():
            code, detail = _get_decision(ctx["url"], event_id)
            assert code == 200
            assert detail["status"] == status, (event_id, status, detail["status"])
        assert set(expected.values()) == ALLOWED_DECISION_STATUSES


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_dismiss_rejected_after_execution_terminal(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Terminal dismiss?"}])
        event_id = r["event_id"]

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?",
                (exec_id,),
            )
            conn.commit()

        code, body = _dismiss(ctx["url"], event_id)
        assert code == 409
        assert body["code"] == "decision.expired"

        code, detail = _get_decision(ctx["url"], event_id)
        assert code == 200
        assert detail["status"] == "expired"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_multiple_question_answer_parts_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r1 = _escalate(ctx["url"], session_id, [{"question": "Q1?"}])
        r2 = _escalate(ctx["url"], session_id, [{"question": "Q2?"}])

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "answering both"},
                    {"data": {"type": "question_answer", "batch_id": r1["batch_id"]}},
                    {"data": {"type": "question_answer", "batch_id": r2["batch_id"]}},
                ]
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "at most one question_answer" in resp.json()["error"]

        data = _get_decisions(ctx["url"])
        statuses = {
            d["batch_id"]: d["status"]
            for d in data["decisions"]
            if d["batch_id"] in (r1["batch_id"], r2["batch_id"])
        }
        assert statuses[r1["batch_id"]] == "pending"
        assert statuses[r2["batch_id"]] == "pending"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_resolved_walk_covers_every_non_pending_decision(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])

        exec_id1, session_id1 = create_execution_via_api(ctx["url"], agent_id)
        r_pending = _escalate(ctx["url"], session_id1, [{"question": "Active Q?"}])

        exec_id2, session_id2 = create_execution_via_api(ctx["url"], agent_id)
        r_answered = _escalate(ctx["url"], session_id2, [{"question": "Answered Q?"}])
        _answer(ctx["url"], session_id2, r_answered["event_id"], "yes")

        exec_id3, session_id3 = create_execution_via_api(ctx["url"], agent_id)
        r_dismissed = _escalate(ctx["url"], session_id3, [{"question": "Dismissed Q?"}])
        _dismiss(ctx["url"], r_dismissed["event_id"])

        exec_id4, session_id4 = create_execution_via_api(ctx["url"], agent_id)
        r_expired = _escalate(ctx["url"], session_id4, [{"question": "Expired Q?"}])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?",
                (exec_id4,),
            )
            conn.commit()

        page = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"state": "resolved"}, timeout=5
        ).json()
        walked = {i["event_id"] for i in page["items"]}
        assert r_answered["event_id"] in walked
        assert r_dismissed["event_id"] in walked
        assert r_expired["event_id"] in walked
        assert r_pending["event_id"] not in walked
        for item in page["items"]:
            assert item["status"] == "resolved"
            assert "answer" not in item
            assert "questions" not in item
            assert "agent_name" not in item


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_resolved_walk_pages_without_duplicates(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        created = []
        for i in range(7):
            r = _escalate(ctx["url"], session_id, [{"question": f"Q{i}?"}])
            _dismiss(ctx["url"], r["event_id"])
            created.append(r["event_id"])

        seen = []
        cursor = None
        for _ in range(10):
            params = {"state": "resolved", "limit": 2}
            if cursor is not None:
                params["before"] = cursor
            page = httpx.get(
                f"{ctx['url']}/api/v1/decisions", params=params, timeout=5
            ).json()
            seen.extend(i["event_id"] for i in page["items"])
            if not page["has_more"]:
                break
            assert page["next_cursor"] is not None
            cursor = page["next_cursor"]

        assert len(seen) == len(set(seen)), "the walk emitted a decision twice"
        assert set(created) <= set(seen)
        positions = [seen.index(e) for e in created]
        assert positions == sorted(positions, reverse=True)


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_resolved_walk_page_one_is_inclusive(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Only one?"}])
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET outcome = 'completed' WHERE id = ?",
                (_exec_id,),
            )
            conn.commit()

        page = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"state": "resolved"}, timeout=5
        ).json()
        assert [i["event_id"] for i in page["items"]] == [r["event_id"]]


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_resolved_limit_bounds_enforced(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        for limit in (0, 201):
            resp = httpx.get(
                f"{ctx['url']}/api/v1/decisions",
                params={"state": "resolved", "limit": limit},
                timeout=5,
            )
            assert resp.status_code == 400
            assert resp.json()["code"] == "request.invalid"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_unknown_state_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/decisions", params={"state": "answered"}, timeout=5
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "request.invalid"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_decisions_execution_filter_scopes_pending(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id1, session_id1 = create_execution_via_api(ctx["url"], agent_id)
        _exec_id2, session_id2 = create_execution_via_api(ctx["url"], agent_id)

        r1 = _escalate(ctx["url"], session_id1, [{"question": "E1-Q?"}])
        _escalate(ctx["url"], session_id2, [{"question": "E2-Q?"}])

        data = _get_decisions(ctx["url"], execution_id=exec_id1)
        assert [d["event_id"] for d in data["decisions"]] == [r1["event_id"]]
