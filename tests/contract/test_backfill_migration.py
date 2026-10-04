# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import subprocess

import httpx
import pytest

from tests.testhelpers import (
    PortManager,
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


def _synthesized(db_url, batch_id):
    data = _marker_data_for_batch(db_url, batch_id)
    if data is None:
        return []
    return [
        {
            "status": "answered",
            "answer": data.get("answer_text"),
            **({"truncated": True} if data.get("truncated") else {}),
        }
    ]


def _insert_historical_answer(db_url, exec_id, session_id, batch_id, text):
    payload = json.dumps(
        {
            "role": "ROLE_USER",
            "parts": [
                {"text": text},
                {"data": {"type": "question_answer", "batch_id": batch_id}},
            ],
        }
    )
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'message', ?)",
            (exec_id, session_id, payload),
        )
        conn.commit()


def _rollback_to_v23(db_url):
    with db_conn(db_url) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (24,))
        conn.commit()


def _has_version(db_url, version=24):
    with db_conn(db_url) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
    return row[0] > 0


def _marker_count(db_url):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE event_type = 'platform'"
        ).fetchall()
    n = 0
    for (payload,) in rows:
        if not isinstance(payload, str):
            continue
        parts = json.loads(payload).get("parts") or []
        if parts and parts[0].get("data", {}).get("type") == "question_answer":
            n += 1
    return n


def _insert_historical_source(db_url, exec_id, session_id, batch_id, content_parts):
    payload = json.dumps(
        {
            "role": "ROLE_USER",
            "parts": content_parts
            + [{"data": {"type": "question_answer", "batch_id": batch_id}}],
        }
    )
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload) "
            "VALUES (?, ?, 'message', ?)",
            (exec_id, session_id, payload),
        )
        conn.commit()


def _insert_source_with_created_at(db_url, exec_id, session_id, batch_id, created_at):
    payload = json.dumps(
        {
            "role": "ROLE_USER",
            "parts": [
                {"text": "legacy"},
                {"data": {"type": "question_answer", "batch_id": batch_id}},
            ],
        }
    )
    with db_conn(db_url) as conn:
        row = conn.execute(
            "INSERT INTO events (execution_id, session_id, event_type, payload, created_at) "
            "VALUES (?, ?, 'message', ?, ?) RETURNING id",
            (exec_id, session_id, payload, created_at),
        ).fetchone()
        conn.commit()
    return row[0]


def _insert_empty_session(db_url, exec_id, agent_id):
    with db_conn(db_url) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, "
            "worktree_path, base_commit_sha, slug, sandbox_policy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "",
                exec_id,
                None,
                agent_id,
                None,
                None,
                None,
                "empty-session-slug",
                '{"fs_level":"unrestricted"}',
            ),
        )
        conn.commit()


def _live_answer(url, session_id, content_parts, event_id):
    return httpx.post(
        f"{url}/api/v1/sessions/{session_id}/message",
        json={
            "parts": content_parts
            + [
                {
                    "data": {
                        "type": "question_answer",
                        "escalation_event_id": event_id,
                    }
                }
            ]
        },
        timeout=10,
    )


def _marker_data_for_batch(db_url, batch_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE event_type = 'platform'"
        ).fetchall()
    for (payload,) in rows:
        if not isinstance(payload, str):
            continue
        parts = json.loads(payload).get("parts") or []
        data = parts[0].get("data", {}) if parts else {}
        if data.get("type") == "question_answer" and data.get("batch_id") == batch_id:
            return data
    return None


def _marker_payload_raw_for_batch(db_url, batch_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE event_type = 'platform'"
        ).fetchall()
    for (payload,) in rows:
        parts = json.loads(payload).get("parts") or []
        data = parts[0].get("data", {}) if parts else {}
        if data.get("type") == "question_answer" and data.get("batch_id") == batch_id:
            return payload
    return None


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


def _max_event_id(db_url):
    with db_conn(db_url) as conn:
        return conn.execute("SELECT MAX(id) FROM events").fetchone()[0]


def _batch_markers(db_url, batch_id):
    with db_conn(db_url) as conn:
        rows = conn.execute(
            "SELECT execution_id, session_id, payload FROM events "
            "WHERE event_type = 'platform'"
        ).fetchall()
    out = []
    for exec_id, session_id, payload in rows:
        parts = json.loads(payload).get("parts") or []
        data = parts[0].get("data", {}) if parts else {}
        if data.get("type") == "question_answer" and data.get("batch_id") == batch_id:
            out.append((exec_id, session_id, data))
    return out


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


_CAP = 96 * 1024


def _marker_json_len(batch_id, source_id, answer_text=None, truncated=False):
    data = {
        "type": "question_answer",
        "batch_id": batch_id,
        "resolved_event_id": source_id,
        "resolved_at": "9999-12-31T23:59:59Z",
    }
    if answer_text is not None:
        data["answer_text"] = answer_text
    if truncated:
        data["truncated"] = True
    payload = {"parts": [{"data": data}]}
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _window_batch_id(source_id):
    lo, hi = 1, _CAP
    while lo <= hi:
        mid = (lo + hi) // 2
        b = "b" * mid
        nt = _marker_json_len(b, source_id, None, False)
        tr = _marker_json_len(b, source_id, None, True)
        if nt <= _CAP and tr > _CAP:
            return b
        if nt > _CAP:
            hi = mid - 1
        else:
            lo = mid + 1
    raise AssertionError("no window batch_id found")


def _largest_batch_id_fitting(source_id, answer_text):
    lo, hi, best = 1, _CAP, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if _marker_json_len("b" * mid, source_id, answer_text, False) <= _CAP:
            best = "b" * mid
            lo = mid + 1
        else:
            hi = mid - 1
    assert best is not None
    return best


def _run_detector(db_url):
    proc = subprocess.run(
        [BIN, "--detect-orphans", "--db-url", db_url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    return proc.returncode, lines, proc.stderr


_EMPTY_ABSENT_CASES = [
    ("multi_text", [{"text": "hello"}, {"text": "second"}], "hello"),
    ("empty_first_then_text", [{"text": ""}, {"text": "later"}], None),
    ("file_only", [{"raw": "QUFB"}], None),
]


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_marker_byte_identical_live_vs_backfill(test_database):
    backfill_batches = {}
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        bf_exec, bf_session = create_execution_via_api(ctx["url"], agent_id)
        for name, content, _ in _EMPTY_ABSENT_CASES:
            b = _escalate(ctx["url"], bf_session, [{"question": f"bf-{name}?"}])[
                "batch_id"
            ]
            backfill_batches[name] = b
            _insert_historical_source(ctx["db_url"], bf_exec, bf_session, b, content)
        _rollback_to_v23(ctx["db_url"])

    with scheduler_context(db_url=test_database) as ctx:
        lv_exec, lv_session = create_execution_via_api(ctx["url"], agent_id)
        live_batches = {}
        for name, content, _ in _EMPTY_ABSENT_CASES:
            r = _escalate(ctx["url"], lv_session, [{"question": f"lv-{name}?"}])
            live_batches[name] = r["batch_id"]
            resp = _live_answer(ctx["url"], lv_session, content, r["event_id"])
            assert resp.status_code == 200, resp.text

        for name, _content, expected in _EMPTY_ABSENT_CASES:
            d_bf = _marker_data_for_batch(test_database, backfill_batches[name])
            d_lv = _marker_data_for_batch(test_database, live_batches[name])
            assert d_bf is not None and d_lv is not None, name
            assert "escalation_event_id" not in d_bf
            assert d_lv["escalation_event_id"] > 0
            for field in (
                "resolved_event_id",
                "resolved_at",
                "batch_id",
                "escalation_event_id",
            ):
                d_bf.pop(field, None)
                d_lv.pop(field, None)
            assert d_bf == d_lv, (name, d_bf, d_lv)
            if expected is None:
                assert "answer_text" not in d_bf
            else:
                assert d_bf["answer_text"] == expected

            bf_dec = _synthesized(test_database, backfill_batches[name])[0]
            lv_dec = _synthesized(test_database, live_batches[name])[0]
            assert bf_dec["answer"] == expected and lv_dec["answer"] == expected


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_synthesizes_marker_on_restart(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Which DB?"}])[
            "batch_id"
        ]
        _insert_historical_answer(
            ctx["db_url"], exec_id, session_id, batch_id, "PostgreSQL"
        )

        found = [
            d
            for d in httpx.get(
                f"{ctx['url']}/api/v1/decisions",
                params={"execution_id": exec_id},
                timeout=10,
            ).json()["decisions"]
            if d["batch_id"] == batch_id
        ]
        assert len(found) == 1
        assert found[0]["status"] == "pending"

        _rollback_to_v23(ctx["db_url"])
        assert not _has_version(ctx["db_url"])

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == 1
        found = _synthesized(test_database, batch_id)
        assert len(found) == 1
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] == "PostgreSQL"
        assert "truncated" not in found[0]

    with scheduler_context(db_url=test_database) as ctx:
        assert _marker_count(ctx["db_url"]) == 1
        found = _synthesized(test_database, batch_id)
        assert found[0]["status"] == "answered"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_all_sources_one_winner(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Pick?"}])[
            "batch_id"
        ]
        _insert_historical_answer(ctx["db_url"], exec_id, session_id, batch_id, "first")
        _insert_historical_answer(
            ctx["db_url"], exec_id, session_id, batch_id, "second"
        )
        _rollback_to_v23(ctx["db_url"])

    with scheduler_context(db_url=test_database) as ctx:
        assert _marker_count(ctx["db_url"]) == 2
        found = _synthesized(test_database, batch_id)
        assert len(found) == 1
        assert found[0]["status"] == "answered"
        assert found[0]["answer"] == "first"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_wrong_identity_marker_is_not_coverage(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec1, session1 = create_execution_via_api(ctx["url"], agent_id)
        exec2, session2 = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session1, [{"question": "Q?"}])["batch_id"]
        _insert_historical_answer(ctx["db_url"], exec1, session1, batch_id, "correct")
        source_id = _max_event_id(test_database)
        _plant_marker(test_database, exec2, session2, batch_id, source_id)
        _rollback_to_v23(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        markers = _batch_markers(test_database, batch_id)
        assert len(markers) == 2
        planted = [m for m in markers if (m[0], m[1]) == (exec2, session2)]
        synthesized = [m for m in markers if (m[0], m[1]) == (exec1, session1)]
        assert len(planted) == 1 and len(synthesized) == 1
        assert synthesized[0][2]["resolved_event_id"] == source_id
        assert synthesized[0][2]["answer_text"] == "correct"

        assert synthesized[0][2]["answer_text"] == "correct"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_truncates_oversize_answer_and_surfaces_flag(test_database):
    big = "z" * (96 * 1024 + 5000)
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Long?"}])[
            "batch_id"
        ]
        _insert_historical_answer(ctx["db_url"], exec_id, session_id, batch_id, big)
        _rollback_to_v23(ctx["db_url"])

    with scheduler_context(db_url=test_database) as ctx:
        assert _marker_count(ctx["db_url"]) == 1
        found = _synthesized(test_database, batch_id)
        assert len(found) == 1
        assert found[0]["status"] == "answered"
        assert found[0]["truncated"] is True
        assert found[0]["answer"] is not None
        assert len(found[0]["answer"]) < len(big)


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_malformed_source_not_synthesized(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        payload = json.dumps(
            {
                "role": "ROLE_USER",
                "parts": [
                    42,
                    {"data": {"type": "question_answer", "batch_id": batch_id}},
                ],
            }
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()
        _rollback_to_v23(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == 0
        assert _synthesized(test_database, batch_id) == []


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_near_cap_short_answer_synthesizes_untruncated(test_database):
    answer = "hello"
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        source_id = (_max_event_id(test_database) or 0) + 2
        batch_id = _largest_batch_id_fitting(source_id, answer)
        _insert_escalation(test_database, exec_id, session_id, batch_id)
        _insert_historical_source(
            test_database, exec_id, session_id, batch_id, [{"text": answer}]
        )
        assert _max_event_id(test_database) == source_id
        _rollback_to_v23(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        markers = _batch_markers(test_database, batch_id)
        assert len(markers) == 1
        _e, _s, data = markers[0]
        assert data["answer_text"] == answer
        assert "truncated" not in data
        assert (
            len(_marker_payload_raw_for_batch(test_database, batch_id).encode()) <= _CAP
        )
        found = _synthesized(test_database, batch_id)[0]
        assert found["status"] == "answered" and found["answer"] == answer
        assert "truncated" not in found

    code, orphans, stderr = _run_detector(test_database)
    assert code == 0 and orphans == []
    assert "0 candidate(s) skipped: no representable marker within cap" in stderr


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_window_absent_answer_synthesizes_and_detector_clean(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        source_id = (_max_event_id(test_database) or 0) + 2
        batch_id = _window_batch_id(source_id)
        _insert_escalation(test_database, exec_id, session_id, batch_id)
        _insert_historical_source(
            test_database, exec_id, session_id, batch_id, [{"raw": "QUFB"}]
        )
        assert _max_event_id(test_database) == source_id
        _rollback_to_v23(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        markers = _batch_markers(test_database, batch_id)
        assert len(markers) == 1
        _e, _s, data = markers[0]
        assert "answer_text" not in data
        assert "truncated" not in data
        assert data["resolved_event_id"] == source_id
        assert (
            len(_marker_payload_raw_for_batch(test_database, batch_id).encode()) <= _CAP
        )
        found = _synthesized(test_database, batch_id)[0]
        assert found["status"] == "answered" and found["answer"] is None

    code, orphans, stderr = _run_detector(test_database)
    assert code == 0 and orphans == []
    assert "0 candidate(s) skipped: no representable marker within cap" in stderr


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_window_long_answer_skipped_by_backfill_and_detector(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        source_id = (_max_event_id(test_database) or 0) + 2
        batch_id = _window_batch_id(source_id)
        _insert_escalation(test_database, exec_id, session_id, batch_id)
        _insert_historical_source(
            test_database, exec_id, session_id, batch_id, [{"text": "x" * 200_000}]
        )
        assert _max_event_id(test_database) == source_id
        _rollback_to_v23(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _batch_markers(test_database, batch_id) == []
        assert _marker_count(test_database) == 0
        assert _synthesized(test_database, batch_id) == []

    code, orphans, stderr = _run_detector(test_database)
    assert code == 0 and orphans == []
    assert "1 candidate(s) skipped: no representable marker within cap" in stderr


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_tool_result_false_positive_not_synthesized(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        payload = json.dumps(
            {
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {
                            "type": "tool_result",
                            "text": "type question_answer here",
                        }
                    }
                ],
            }
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, payload),
            )
            conn.commit()
        _rollback_to_v23(ctx["db_url"])

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == 0


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_truncates_multibyte_answer_at_char_boundary(test_database):
    cap = 96 * 1024
    emoji = "😀"
    emoji_bytes = len(emoji.encode("utf-8"))
    big = emoji * (40 * 1024)
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Long multibyte?"}])[
            "batch_id"
        ]
        _insert_historical_source(
            ctx["db_url"], exec_id, session_id, batch_id, [{"text": big}]
        )
        _rollback_to_v23(ctx["db_url"])

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        data = _marker_data_for_batch(test_database, batch_id)
        assert data is not None
        assert data["truncated"] is True
        answer = data["answer_text"]
        assert answer and set(answer) == {emoji}
        assert len(answer) < len(big)

        raw = _marker_payload_raw_for_batch(test_database, batch_id)
        stored = len(raw.encode("utf-8"))
        assert stored <= cap
        assert cap - stored < emoji_bytes

        found = _synthesized(test_database, batch_id)[0]
        assert found["status"] == "answered"
        assert found["truncated"] is True
        assert found["answer"] == answer


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_map_cap_overflow_fails_then_succeeds_on_retry(test_database):
    n_sources = 80
    long_prefix = "x" * 20000
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        for i in range(n_sources):
            b = f"{long_prefix}-{i}"
            _insert_escalation(ctx["db_url"], exec_id, session_id, b)
            _insert_historical_source(
                ctx["db_url"], exec_id, session_id, b, [{"text": f"ans{i}"}]
            )
        _rollback_to_v23(test_database)
        assert not _has_version(test_database)

    env = {**os.environ, "AGENTBEACON_BACKFILL_MAP_CAP_MIB": "1"}
    proc = subprocess.run(
        [
            BIN,
            "--port",
            str(PortManager().allocate_port()),
            "--db-url",
            test_database,
            "--max-workers",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 1
    output = proc.stdout + proc.stderr
    assert "retained key bytes" in output
    assert "AGENTBEACON_BACKFILL_MAP_CAP_MIB" in output

    assert not _has_version(test_database)
    assert _marker_count(test_database) == 0

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == n_sources


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_aborts_on_non_positive_source_id(test_database):
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
        _rollback_to_v23(test_database)

    proc = subprocess.run(
        [
            BIN,
            "--port",
            str(PortManager().allocate_port()),
            "--db-url",
            test_database,
            "--max-workers",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 1
    output = proc.stdout + proc.stderr
    assert "non-positive id (-1)" in output
    assert not _has_version(test_database)
    assert _marker_count(test_database) == 0

    with db_conn(test_database) as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (-1,))
        conn.commit()
    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_backfill_failpoint_after_flush_rolls_back_partial_markers(test_database):
    n_sources = 250
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        for i in range(n_sources):
            b = f"flush-batch-{i}"
            _insert_escalation(test_database, exec_id, session_id, b)
            _insert_historical_source(
                test_database, exec_id, session_id, b, [{"text": f"ans{i}"}]
            )
        _rollback_to_v23(test_database)
        assert not _has_version(test_database)

    markers_before = _marker_count(test_database)

    env = {**os.environ, "AGENTBEACON_TEST_FAIL_AT": "backfill_after_flush"}
    proc = subprocess.run(
        [
            BIN,
            "--port",
            str(PortManager().allocate_port()),
            "--db-url",
            test_database,
            "--max-workers",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 1
    output = proc.stdout + proc.stderr
    assert "test failpoint at backfill_after_flush" in output

    assert not _has_version(test_database)
    assert _marker_count(test_database) == markers_before

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(test_database) == n_sources


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_backfill_aborts_on_unparseable_created_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        source_id = _insert_source_with_created_at(
            test_database, exec_id, session_id, batch_id, "not-a-timestamp"
        )
        _rollback_to_v23(test_database)
        assert not _has_version(test_database)

    proc = subprocess.run(
        [
            BIN,
            "--port",
            str(PortManager().allocate_port()),
            "--db-url",
            test_database,
            "--max-workers",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 1
    output = proc.stdout + proc.stderr
    assert "unparseable created_at" in output
    assert f"source event {source_id}" in output
    assert not _has_version(test_database)
    assert _marker_count(test_database) == 0

    with db_conn(test_database) as conn:
        conn.execute(
            "UPDATE events SET created_at = ? WHERE id = ?",
            ("2025-01-01 00:00:00", source_id),
        )
        conn.commit()
    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == 1


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_detector_errors_on_unparseable_created_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = "orphan-created-at"
        _insert_escalation(test_database, exec_id, session_id, batch_id)
        source_id = _insert_source_with_created_at(
            test_database, exec_id, session_id, batch_id, "not-a-timestamp"
        )
        assert _has_version(test_database)

    code, lines, stderr = _run_detector(test_database)
    assert code == 2
    assert "unparseable created_at" in stderr
    assert f"source event {source_id}" in stderr
    assert lines == []


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_backfill_aborts_on_non_rfc3339_created_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        source_id = _insert_source_with_created_at(
            test_database, exec_id, session_id, batch_id, "10000-01-01 00:00:00+00"
        )
        _rollback_to_v23(test_database)
        assert not _has_version(test_database)

    proc = subprocess.run(
        [
            BIN,
            "--port",
            str(PortManager().allocate_port()),
            "--db-url",
            test_database,
            "--max-workers",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 1
    output = proc.stdout + proc.stderr
    assert "unparseable created_at" in output
    assert f"source event {source_id}" in output
    assert not _has_version(test_database)
    assert _marker_count(test_database) == 0

    with db_conn(test_database) as conn:
        conn.execute(
            "UPDATE events SET created_at = ? WHERE id = ?",
            ("2025-01-01 00:00:00+00", source_id),
        )
        conn.commit()
    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == 1


@pytest.mark.parametrize("test_database", ["postgres"], indirect=True)
def test_detector_errors_on_non_rfc3339_created_at(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        batch_id = "orphan-year-10000"
        _insert_escalation(test_database, exec_id, session_id, batch_id)
        source_id = _insert_source_with_created_at(
            test_database, exec_id, session_id, batch_id, "10000-01-01 00:00:00+00"
        )
        assert _has_version(test_database)

    code, lines, stderr = _run_detector(test_database)
    assert code == 2
    assert "unparseable created_at" in stderr
    assert f"source event {source_id}" in stderr
    assert lines == []


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_backfill_skips_invalid_utf8_blob_payloads(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        good_batch = _escalate(ctx["url"], session_id, [{"question": "Q?"}])["batch_id"]
        _insert_historical_source(
            test_database, exec_id, session_id, good_batch, [{"text": "good"}]
        )
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, b"\x80"),
            )
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'message', ?)",
                (exec_id, session_id, b"\x80question_answer"),
            )
            conn.commit()
        _rollback_to_v23(test_database)
        assert not _has_version(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        assert _marker_count(ctx["db_url"]) == 1
        answered = _synthesized(test_database, good_batch)[0]
        assert answered["status"] == "answered"

    code, lines, stderr = _run_detector(test_database)
    assert code == 0
    assert lines == []


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_answer_and_decisions_tolerate_corrupt_blob_in_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id)
        r = _escalate(ctx["url"], session_id, [{"question": "Q?"}])
        event_id = r["event_id"]
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, 'platform', ?)",
                (exec_id, session_id, b"\x80"),
            )
            conn.commit()

        resp = _live_answer(ctx["url"], session_id, [{"text": "Postgres"}], event_id)
        assert resp.status_code == 200, resp.text

        detail = httpx.get(f"{ctx['url']}/api/v1/decisions/{event_id}", timeout=10)
        assert detail.status_code == 200
        assert detail.json()["status"] == "answered"
        assert detail.json()["answer"] == "Postgres"


@pytest.mark.parametrize("test_database", BACKENDS, indirect=True)
def test_null_session_marker_does_not_cover_empty_string_owner(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"])
        exec_id, _real_session = create_execution_via_api(ctx["url"], agent_id)
        _insert_empty_session(test_database, exec_id, agent_id)
        batch_id = "empty-owner-batch"
        _insert_escalation(test_database, exec_id, "", batch_id)
        _insert_historical_source(
            test_database, exec_id, "", batch_id, [{"text": "real"}]
        )
        source_id = _max_event_id(test_database)
        _plant_marker(test_database, exec_id, None, batch_id, source_id)
        _rollback_to_v23(test_database)

    with scheduler_context(db_url=test_database) as ctx:
        assert _has_version(ctx["db_url"])
        markers = _batch_markers(test_database, batch_id)
        synthesized = [m for m in markers if m[1] == ""]
        null_session = [m for m in markers if m[1] is None]
        assert len(synthesized) == 1 and len(null_session) == 1
        assert synthesized[0][2]["resolved_event_id"] == source_id
        assert synthesized[0][2]["answer_text"] == "real"

        assert synthesized[0][2]["answer_text"] == "real"

    code, lines, stderr = _run_detector(test_database)
    assert code == 0
    assert lines == []
