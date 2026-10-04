# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
from datetime import datetime, timezone

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

BACKENDS = ["sqlite", "postgres"]


def _repo_utc(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _escalate(url, session_id, questions, importance="blocking"):
    resp = httpx.post(
        f"{url}/api/v1/escalate",
        json={"questions": questions, "importance": importance},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"escalate failed: {resp.status_code} {resp.text}"
    return resp.json()


def _answer(url, session_id, parts):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={"parts": parts},
        timeout=10,
    )


def _answer_text(url, session_id, event_id, text):
    return _answer(
        url,
        session_id,
        [
            {"text": text},
            {"data": {"type": "question_answer", "escalation_event_id": event_id}},
        ],
    )


def _get_decision(url, event_id):
    resp = httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _session_events(url, session_id):
    resp = httpx.get(f"{url}/api/v1/sessions/{session_id}/events", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _markers(events):
    out = []
    for e in events:
        if e["event_type"] != "platform":
            continue
        parts = e.get("payload", {}).get("parts") or []
        if parts and parts[0].get("data", {}).get("type") == "question_answer":
            out.append(e)
    return out


def _get_decisions(url, **params):
    resp = httpx.get(f"{url}/api/v1/decisions", params=params, timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _plant_marker(db_url, exec_id, session_id, escalation_event_id, resolved_event_id):
    payload = json.dumps(
        {
            "parts": [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": "planted-batch",
                        "escalation_event_id": int(escalation_event_id),
                        "resolved_event_id": resolved_event_id,
                        "resolved_at": "2025-01-01T00:00:00Z",
                        "answer_text": "planted",
                    }
                }
            ]
        }
    )
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'platform', ?)",
            (exec_id, session_id, payload),
        )
        conn.commit()


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_allowed_despite_wrong_identity_marker(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec1, session1 = create_execution_via_api(ctx["url"], agent_id)
        exec2, session2 = create_execution_via_api(ctx["url"], agent_id)
        event_id = _escalate(ctx["url"], session1, [{"question": "Owned?"}])["event_id"]

        _plant_marker(test_database, exec2, session2, event_id, 999999)
        r1 = _answer_text(ctx["url"], session1, event_id, "real")
        assert r1.status_code == 200, r1.text

        r2 = _answer_text(ctx["url"], session1, event_id, "again")
        assert r2.status_code == 409
        assert r2.json()["code"] == "decision.already_resolved"

        found = [_get_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] == "real"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_writes_marker_with_source_id_and_text(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)

        r = _escalate(ctx["url"], session_id, [{"question": "Which DB?"}])
        event_id = r["event_id"]

        resp = _answer_text(ctx["url"], session_id, event_id, "PostgreSQL")
        assert resp.status_code == 200, resp.text
        message_event_id = resp.json()["event_id"]

        events = _session_events(ctx["url"], session_id)
        markers = _markers(events)
        assert len(markers) == 1, f"expected exactly one marker, got {len(markers)}"
        data = markers[0]["payload"]["parts"][0]["data"]
        assert data["escalation_event_id"] == int(event_id)
        assert data["resolved_event_id"] == int(message_event_id)
        assert markers[0]["id"] != message_event_id
        assert data["answer_text"] == "PostgreSQL"
        assert "truncated" not in data

        msg = next(e for e in events if e["id"] == message_event_id)
        source_ts = _repo_utc(msg["created_at"])
        assert data["resolved_at"] == source_ts

        found = [_get_decision(ctx["url"], event_id)]
        assert len(found) == 1
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] == "PostgreSQL"
        assert found[0]["answered_at"] == source_ts


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_marker_written_once_and_delivered(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])

        resp = _answer_text(ctx["url"], session_id, r["event_id"], "yes")
        assert resp.status_code == 200

        events = _session_events(ctx["url"], session_id)
        assert len(_markers(events)) == 1
        delivered = [
            e
            for e in events
            if e["event_type"] == "platform"
            and (e.get("payload", {}).get("type") == "message_delivered")
        ]
        assert len(delivered) == 1


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_empty_answer_omits_marker_key_and_returns_null(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        event_id = r["event_id"]

        resp = _answer(
            ctx["url"],
            session_id,
            [
                {"text": ""},
                {"raw": "QUFB"},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ],
        )
        assert resp.status_code == 200, resp.text

        markers = _markers(_session_events(ctx["url"], session_id))
        assert len(markers) == 1
        data = markers[0]["payload"]["parts"][0]["data"]
        assert "answer_text" not in data

        found = [_get_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] is None


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_missing_event_id_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        resp = _answer(
            ctx["url"],
            session_id,
            [
                {"text": "hi"},
                {"data": {"type": "question_answer", "escalation_event_id": 123}},
            ],
        )
        assert resp.status_code == 400
        assert (
            "question_answer part requires a string escalation_event_id"
            in resp.json()["error"]
        )


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_empty_event_id_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        resp = _answer(
            ctx["url"],
            session_id,
            [
                {"text": "hi"},
                {"data": {"type": "question_answer", "escalation_event_id": ""}},
            ],
        )
        assert resp.status_code == 400
        assert (
            "question_answer part requires a string escalation_event_id"
            in resp.json()["error"]
        )
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0
        found = [
            d
            for d in _get_decisions(ctx["url"])["decisions"]
            if d["event_id"] == r["event_id"]
        ]
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_sender_part_rejected_direct_route(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        resp = _answer(
            ctx["url"],
            session_id,
            [
                {"text": "ans"},
                {
                    "data": {
                        "type": "question_answer",
                        "escalation_event_id": r["event_id"],
                    }
                },
                {"data": {"type": "sender", "name": "peer", "session_id": "x"}},
            ],
        )
        assert resp.status_code == 400
        assert "question_answer cannot carry a sender part" in resp.json()["error"]
        found = [
            d
            for d in _get_decisions(ctx["url"])["decisions"]
            if d["event_id"] == r["event_id"]
        ]
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_with_scalar_part_rejected_no_side_effects(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        event_id = r["event_id"]

        resp = _answer(
            ctx["url"],
            session_id,
            [
                42,
                {"text": "real"},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ],
        )
        assert resp.status_code == 400
        assert (
            "message parts must be objects with object data members"
            in resp.json()["error"]
        )
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0
        found = [_get_decision(ctx["url"], event_id)]
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_oversize_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        huge = "x" * 100_000
        resp = _answer_text(ctx["url"], session_id, r["event_id"], huge)
        assert resp.status_code == 400
        assert resp.json()["code"] == "decision.answer_too_large"
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_ownership_mismatch_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id1, session_id1 = create_execution_via_api(ctx["url"], agent_id)
        _exec_id2, session_id2 = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id1, [{"question": "Q?"}])
        resp = _answer_text(ctx["url"], session_id2, r["event_id"], "wrong session")
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.wrong_session"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_rejects_agent_session_bearer(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": [
                    {"text": "ans"},
                    {
                        "data": {
                            "type": "question_answer",
                            "escalation_event_id": r["event_id"],
                        }
                    },
                ]
            },
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=10,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"


def _post(url, session_id, parts, bearer=None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else None
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={"parts": parts},
        headers=headers,
        timeout=10,
    )


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_multi_answer_precedes_domain(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "x"},
                42,
                {"data": {"type": "question_answer", "escalation_event_id": "1"}},
                {"data": {"type": "question_answer", "escalation_event_id": "2"}},
            ],
        )
        assert resp.status_code == 400
        assert (
            "message must contain at most one question_answer part"
            in resp.json()["error"]
        )
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_missing_batch_precedes_sender(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "x"},
                {"data": {"type": "question_answer"}},
                {"data": {"type": "sender", "name": "peer", "session_id": "x"}},
            ],
        )
        assert resp.status_code == 400
        assert (
            "question_answer part requires a string escalation_event_id"
            in resp.json()["error"]
        )
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_sender_precedes_size(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "x" * 100_000},
                {"data": {"type": "question_answer", "escalation_event_id": "1"}},
                {"data": {"type": "sender", "name": "peer", "session_id": "x"}},
            ],
        )
        assert resp.status_code == 400
        assert "question_answer cannot carry a sender part" in resp.json()["error"]
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_size_precedes_agent_bearer_403(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "x" * 100_000},
                {
                    "data": {
                        "type": "question_answer",
                        "escalation_event_id": r["event_id"],
                    }
                },
            ],
            bearer=session_id,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "decision.answer_too_large"
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_ownership_precedes_agent_bearer_403(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec1, session1 = create_execution_via_api(ctx["url"], agent_id)
        _exec2, session2 = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session1, [{"question": "Q?"}])
        resp = _post(
            ctx["url"],
            session2,
            [
                {"text": "ans"},
                {
                    "data": {
                        "type": "question_answer",
                        "escalation_event_id": r["event_id"],
                    }
                },
            ],
            bearer=session2,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.wrong_session"
        assert len(_markers(_session_events(ctx["url"], session2))) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_unknown_batch_precedes_agent_bearer_403(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "ans"},
                {"data": {"type": "question_answer", "escalation_event_id": "999999"}},
            ],
            bearer=session_id,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "decision.not_found"
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0


def _mark_execution_terminal(db_url, exec_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE executions SET outcome = 'completed' WHERE id = ?", (exec_id,)
        )
        conn.commit()


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_agent_bearer_precedes_already_resolved_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        assert (
            _answer_text(ctx["url"], session_id, r["event_id"], "real").status_code
            == 200
        )
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "again"},
                {
                    "data": {
                        "type": "question_answer",
                        "escalation_event_id": r["event_id"],
                    }
                },
            ],
            bearer=session_id,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"
        assert len(_markers(_session_events(ctx["url"], session_id))) == 1
        assert _get_decision(ctx["url"], r["event_id"])["answer"] == "real"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_write_barrier_precedes_already_resolved_409(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        assert (
            _answer_text(ctx["url"], session_id, r["event_id"], "real").status_code
            == 200
        )
        _mark_execution_terminal(test_database, exec_id)
        resp = _answer_text(ctx["url"], session_id, r["event_id"], "again")
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.already_resolved"
        assert len(_markers(_session_events(ctx["url"], session_id))) == 1


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_compound_agent_bearer_precedes_write_barrier_403(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        _mark_execution_terminal(test_database, exec_id)
        resp = _post(
            ctx["url"],
            session_id,
            [
                {"text": "ans"},
                {
                    "data": {
                        "type": "question_answer",
                        "escalation_event_id": r["event_id"],
                    }
                },
            ],
            bearer=session_id,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "auth.forbidden"
        assert len(_markers(_session_events(ctx["url"], session_id))) == 0
