# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)

BACKENDS = ["sqlite", "postgres"]


def _escalate(url, session_id, questions):
    resp = httpx.post(
        f"{url}/api/v1/escalate",
        json={"questions": questions, "importance": "blocking"},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _decisions(url, exec_id):
    resp = httpx.get(
        f"{url}/api/v1/decisions", params={"execution_id": exec_id}, timeout=10
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["decisions"]


def _decision(url, event_id):
    resp = httpx.get(f"{url}/api/v1/decisions/{event_id}", timeout=10)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _insert_platform_event(db_url, exec_id, session_id, parts, role=None):
    payload = {"parts": parts}
    if role is not None:
        payload["role"] = role
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'platform', ?)",
            (exec_id, session_id, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()


def _answer_parts(text, event_id):
    return [
        {"text": text},
        {"data": {"type": "question_answer", "escalation_event_id": event_id}},
    ]


def _event_count(db_url, exec_id):
    with db_conn(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE execution_id = ?", (exec_id,)
        ).fetchone()[0]


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_body_over_2mib_is_413_before_handler(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        before = _event_count(test_database, exec_id)
        huge_text = "x" * (2 * 1024 * 1024 + 1024)
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": [{"text": huge_text}]},
            timeout=30,
        )
        assert resp.status_code == 413
        assert _event_count(test_database, exec_id) == before


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_multipart_later_part_over_2mib_is_413(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        before = _event_count(test_database, exec_id)
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={
                "parts": _answer_parts("small answer", r["event_id"])
                + [{"text": "z" * (2 * 1024 * 1024 + 1024)}]
            },
            timeout=30,
        )
        assert resp.status_code == 413
        assert _event_count(test_database, exec_id) == before
        found = [
            d for d in _decisions(ctx["url"], exec_id) if d["event_id"] == r["event_id"]
        ]
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_oversize_marker_under_2mib_is_handler_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        before = _event_count(test_database, exec_id)
        answer = "y" * (200 * 1024)
        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{session_id}/message",
            json={"parts": _answer_parts(answer, r["event_id"])},
            timeout=30,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "decision.answer_too_large"
        assert _event_count(test_database, exec_id) == before
        found = [
            d for d in _decisions(ctx["url"], exec_id) if d["event_id"] == r["event_id"]
        ]
        assert found[0]["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_multi_dismiss_row_dismisses_multiple_decisions(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r1 = _escalate(ctx["url"], session_id, [{"question": "Q1?"}])
        r2 = _escalate(ctx["url"], session_id, [{"question": "Q2?"}])
        _insert_platform_event(
            test_database,
            exec_id,
            session_id,
            [
                {
                    "data": {
                        "type": "question_dismiss",
                        "batch_id": r1["batch_id"],
                        "escalation_event_id": int(r1["event_id"]),
                    }
                },
                {
                    "data": {
                        "type": "question_dismiss",
                        "batch_id": r2["batch_id"],
                        "escalation_event_id": int(r2["event_id"]),
                    }
                },
            ],
        )
        assert _decisions(ctx["url"], exec_id) == []
        assert _decision(ctx["url"], r1["event_id"])["status"] == "dismissed"
        assert _decision(ctx["url"], r2["event_id"])["status"] == "dismissed"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_marker_naming_a_different_escalation_does_not_attach(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        target = _escalate(ctx["url"], session_id, [{"question": "Target?"}])
        other = _escalate(ctx["url"], session_id, [{"question": "Other?"}])

        _insert_platform_event(
            test_database,
            exec_id,
            session_id,
            [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": target["batch_id"],
                        "escalation_event_id": int(other["event_id"]),
                        "resolved_event_id": 999001,
                        "resolved_at": "2025-01-01T00:00:00Z",
                        "answer_text": "misdirected",
                    }
                }
            ],
        )

        assert _decision(ctx["url"], target["event_id"])["status"] == "pending"
        assert _decision(ctx["url"], other["event_id"])["status"] == "answered"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_marker_without_escalation_event_id_never_attaches(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Legacy?"}])

        _insert_platform_event(
            test_database,
            exec_id,
            session_id,
            [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": r["batch_id"],
                        "resolved_event_id": 999002,
                        "resolved_at": "2025-01-01T00:00:00Z",
                        "answer_text": "legacy",
                    }
                }
            ],
        )

        assert _decision(ctx["url"], r["event_id"])["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_empty_string_answer_text_marker_is_null(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r_empty = _escalate(ctx["url"], session_id, [{"question": "empty?"}])
        r_omit = _escalate(ctx["url"], session_id, [{"question": "omit?"}])

        def marker(ref, text=None):
            data = {
                "type": "question_answer",
                "batch_id": ref["batch_id"],
                "escalation_event_id": int(ref["event_id"]),
                "resolved_event_id": 999001,
                "resolved_at": "2025-01-01T00:00:00Z",
            }
            if text is not None:
                data["answer_text"] = text
            return [{"data": data}]

        _insert_platform_event(test_database, exec_id, session_id, marker(r_empty, ""))
        _insert_platform_event(test_database, exec_id, session_id, marker(r_omit))

        d_empty = _decision(ctx["url"], r_empty["event_id"])
        d_omit = _decision(ctx["url"], r_omit["event_id"])
        assert d_empty["status"] == "answered" and d_empty["answer"] is None
        assert d_omit["status"] == "answered" and d_omit["answer"] is None


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_duplicate_exact_source_marker_tiebreak(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Dup?"}])

        def marker(text):
            return [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": r["batch_id"],
                        "escalation_event_id": int(r["event_id"]),
                        "resolved_event_id": 999999,
                        "resolved_at": "2025-01-01T00:00:00Z",
                        "answer_text": text,
                    }
                }
            ]

        _insert_platform_event(test_database, exec_id, session_id, marker("winner"))
        _insert_platform_event(test_database, exec_id, session_id, marker("loser"))

        detail = _decision(ctx["url"], r["event_id"])
        assert detail["status"] == "answered"
        assert detail["answer"] == "winner"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_marker_under_wrong_session_is_ignored(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Owned?"}])

        other_session = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'sibling', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (other_session, exec_id, session_id, agent_id),
            )
            conn.commit()

        _insert_platform_event(
            test_database,
            exec_id,
            other_session,
            [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": r["batch_id"],
                        "escalation_event_id": int(r["event_id"]),
                        "resolved_event_id": 42,
                        "resolved_at": "2025-01-01T00:00:00Z",
                        "answer_text": "wrong-owner",
                    }
                }
            ],
        )

        assert _decision(ctx["url"], r["event_id"])["status"] == "pending"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_answer_from_sibling_session_is_rejected(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Owned?"}])

        other_session = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'sibling2', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (other_session, exec_id, session_id, agent_id),
            )
            conn.commit()

        resp = httpx.post(
            f"{ctx['url']}/api/v1/sessions/{other_session}/message",
            json={"parts": _answer_parts("sneaky", r["event_id"])},
            timeout=10,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "decision.wrong_session"
        assert _decision(ctx["url"], r["event_id"])["status"] == "pending"
