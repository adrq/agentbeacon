# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import subprocess
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
BIN = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "bin", "agentbeacon")
)


def _escalate(url, session_id, questions):
    resp = httpx.post(
        f"{url}/api/v1/escalate",
        json={"questions": questions, "importance": "blocking"},
        headers={"Authorization": f"Bearer {session_id}"},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _answer(url, session_id, event_id, text="ans"):
    resp = httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": [
                {"text": text},
                {"data": {"type": "question_answer", "escalation_event_id": event_id}},
            ]
        },
        timeout=10,
    )
    assert resp.status_code == 200, resp.text


def _insert_message_event(db_url, exec_id, session_id, payload_obj):
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'message', ?)",
            (exec_id, session_id, json.dumps(payload_obj)),
        )
        conn.commit()
    with db_conn(db_url) as conn:
        return conn.execute("SELECT MAX(id) FROM events").fetchone()[0]


def _legacy_answer(db_url, exec_id, session_id, batch_id, text="legacy"):
    return _insert_message_event(
        db_url,
        exec_id,
        session_id,
        {
            "role": "ROLE_USER",
            "parts": [
                {"text": text},
                {"data": {"type": "question_answer", "batch_id": batch_id}},
            ],
        },
    )


def _plant_marker(db_url, exec_id, session_id, batch_id, resolved_event_id):
    payload = json.dumps(
        {
            "parts": [
                {
                    "data": {
                        "type": "question_answer",
                        "batch_id": batch_id,
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


def _insert_escalation(db_url, exec_id, session_id, batch_id):
    payload = json.dumps(
        {
            "role": "ROLE_AGENT",
            "parts": [
                {
                    "data": {
                        "type": "escalate",
                        "batch_id": batch_id,
                        "batch_size": 1,
                        "batch_index": 0,
                    }
                }
            ],
        }
    )
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'platform', ?)",
            (exec_id, session_id, payload),
        )
        conn.commit()


def _schema_version(db_url):
    with db_conn(db_url) as conn:
        return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]


def _run_detector(db_url):
    proc = subprocess.run(
        [BIN, "--detect-orphans", "--db-url", db_url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    return proc.returncode, lines, proc.stderr


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_clean_after_live_answer(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        _exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        _answer(ctx["url"], session_id, r["event_id"], "PostgreSQL")

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 0
    assert orphans == []


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_reports_legacy_orphan(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        source_id = _legacy_answer(test_database, exec_id, session_id, batch_id)
        resp = httpx.get(
            f"{ctx['url']}/api/v1/decisions",
            params={"execution_id": exec_id},
            timeout=10,
        )
        found = [d for d in resp.json()["decisions"] if d["batch_id"] == batch_id]
        assert found[0]["status"] == "pending"

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 1
    assert len(orphans) == 1
    o = orphans[0]
    assert o["schema_version"] == _schema_version(test_database)
    assert o["owner_execution_id"] == exec_id
    assert o["owner_session_id"] == session_id
    assert o["batch_id"] == batch_id
    assert o["source_event_id"] == source_id


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_orphans_emitted_in_source_event_id_order(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec1, session1 = create_execution_via_api(ctx["url"], agent_id)
        exec2, session2 = create_execution_via_api(ctx["url"], agent_id)

        plan = [
            (exec1, session1, "orphan-d"),
            (exec2, session2, "orphan-c"),
            (exec1, session1, "orphan-b"),
            (exec2, session2, "orphan-a"),
        ]
        expected = []
        for exec_id, session_id, batch_id in plan:
            _insert_escalation(test_database, exec_id, session_id, batch_id)
            src = _legacy_answer(test_database, exec_id, session_id, batch_id)
            expected.append((src, exec_id, session_id, batch_id))

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 1
    assert len(orphans) == len(expected)

    ids = [o["source_event_id"] for o in orphans]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    emitted = [
        (
            o["source_event_id"],
            o["owner_execution_id"],
            o["owner_session_id"],
            o["batch_id"],
        )
        for o in orphans
    ]
    assert emitted == sorted(expected, key=lambda t: t[0])
    assert [o["batch_id"] for o in orphans] == [
        "orphan-d",
        "orphan-c",
        "orphan-b",
        "orphan-a",
    ]
    assert [o["owner_execution_id"] for o in orphans] == [exec1, exec2, exec1, exec2]


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_wrong_identity_marker_still_orphan(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec1, session1 = create_execution_via_api(ctx["url"], agent_id)
        exec2, session2 = create_execution_via_api(ctx["url"], agent_id)

        batch_a = _escalate(ctx["url"], session1, [{"question": "A?"}])["batch_id"]
        src_a = _legacy_answer(test_database, exec1, session1, batch_a)
        _plant_marker(test_database, exec2, session2, batch_a, src_a)

        batch_b = _escalate(ctx["url"], session1, [{"question": "B?"}])["batch_id"]
        src_b = _legacy_answer(test_database, exec1, session1, batch_b)
        sibling = str(uuid.uuid4())
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, "
                "slug, desired, executor_state, metadata, sandbox_policy) "
                "VALUES (?, ?, ?, ?, 'sibling', 'run', 'unassigned', '{}', "
                '\'{"fs_level":"unrestricted"}\')',
                (sibling, exec1, session1, agent_id),
            )
            conn.commit()
        _plant_marker(test_database, exec1, sibling, batch_b, src_b)

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 1
    by_batch = {o["batch_id"]: o for o in orphans}
    assert set(by_batch) == {batch_a, batch_b}
    assert by_batch[batch_a] == {
        "schema_version": _schema_version(test_database),
        "owner_execution_id": exec1,
        "owner_session_id": session1,
        "batch_id": batch_a,
        "source_event_id": src_a,
    }
    assert by_batch[batch_b] == {
        "schema_version": _schema_version(test_database),
        "owner_execution_id": exec1,
        "owner_session_id": session1,
        "batch_id": batch_b,
        "source_event_id": src_b,
    }


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_schema_below_24_exits_2(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        _legacy_answer(test_database, exec_id, session_id, batch_id)
        with db_conn(test_database) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (24,))
            conn.commit()

    code, orphans, stderr = _run_detector(test_database)
    assert code == 2
    assert orphans == []
    assert "below 24" in stderr


def test_detector_missing_sqlite_file_exits_2_without_creating(tmp_path):
    missing = tmp_path / "nonexistent.db"
    proc = subprocess.run(
        [BIN, "--detect-orphans", "--db-url", f"sqlite:{missing}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert not missing.exists(), "detector must not create the database file"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_unknown_owner_not_orphan(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        _legacy_answer(test_database, exec_id, session_id, "orphan-no-owner-batch")

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 0
    assert orphans == []


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_multi_answer_not_orphan(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        b1 = _escalate(ctx["url"], session_id, [{"question": "Q1?"}])["batch_id"]
        b2 = _escalate(ctx["url"], session_id, [{"question": "Q2?"}])["batch_id"]
        _insert_message_event(
            test_database,
            exec_id,
            session_id,
            {
                "role": "ROLE_USER",
                "parts": [
                    {"data": {"type": "question_answer", "batch_id": b1}},
                    {"data": {"type": "question_answer", "batch_id": b2}},
                ],
            },
        )

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 0
    assert orphans == []


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_reports_non_positive_id_source(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        payload = json.dumps(
            {
                "role": "ROLE_USER",
                "parts": [
                    {"text": "legacy"},
                    {"data": {"type": "question_answer", "batch_id": batch_id}},
                ],
            }
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (id, execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, ?, 'message', ?)",
                (-1, exec_id, session_id, payload),
            )
            conn.commit()

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 1
    assert len(orphans) == 1
    assert orphans[0]["batch_id"] == batch_id
    assert orphans[0]["source_event_id"] == -1


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_malformed_source_not_orphan(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        _insert_message_event(
            test_database,
            exec_id,
            session_id,
            {
                "role": "ROLE_USER",
                "parts": [
                    42,
                    {"data": {"type": "question_answer", "batch_id": batch_id}},
                ],
            },
        )

    code, orphans, _stderr = _run_detector(test_database)
    assert code == 0
    assert orphans == []


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_detector_envelope_oversize_not_orphan(test_database):
    giant = "b" * (96 * 1024 + 50)
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        payload = json.dumps(
            {
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "data": {
                            "type": "escalate",
                            "batch_id": giant,
                            "batch_size": 1,
                            "batch_index": 0,
                        }
                    }
                ],
            }
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()
        _legacy_answer(test_database, exec_id, session_id, giant)

    code, orphans, stderr = _run_detector(test_database)
    assert code == 0
    assert orphans == []
    assert "1 candidate(s) skipped: no representable marker within cap" in stderr
