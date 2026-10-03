# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import threading
import time

import httpx
import pytest

from tests.dual_backend import DUAL_BACKEND
from tests.mock_agent_helpers import rest_escalate_ok
from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    scheduler_context,
    seed_test_agent,
)


@DUAL_BACKEND
def test_an_unknown_execution_is_a_problem(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/no-such-execution/events/stream",
            timeout=10,
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["code"] == "resource.not_found"
        assert body["status"] == 404
        assert not body["code"].startswith("stream.")


@DUAL_BACKEND
def test_a_wrong_method_on_the_stream_is_a_problem(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions/anything/events/stream", timeout=10
        )
        assert resp.status_code == 405
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "request.method_not_allowed"


@DUAL_BACKEND
def test_a_database_failure_is_not_reported_as_a_missing_execution(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-db-failure")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        with httpx.stream(
            "GET",
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
            timeout=10,
        ) as ok:
            assert ok.status_code == 200

        with db_conn(test_database) as conn:
            conn.execute("ALTER TABLE executions RENAME TO executions_unreachable")
            conn.commit()
        try:
            resp = httpx.get(
                f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream", timeout=10
            )
        finally:
            with db_conn(test_database) as conn:
                conn.execute("ALTER TABLE executions_unreachable RENAME TO executions")
                conn.commit()

        assert resp.status_code == 500
        assert not resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert set(body) == {"error"}
        assert "executions" in body["error"], body


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_the_stream_advances_past_a_corrupt_run(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-corrupt")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        lines = []
        done = threading.Event()

        def collect():
            try:
                with httpx.stream(
                    "GET",
                    f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                    timeout=20,
                ) as resp:
                    for line in resp.iter_lines():
                        lines.append(line)
            except httpx.ReadTimeout:
                pass
            done.set()

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        time.sleep(1.0)

        with db_conn(test_database) as conn:
            for _ in range(3):
                conn.execute(
                    "INSERT INTO events (execution_id, session_id, event_type, payload) "
                    "VALUES (?, ?, 'message', ?)",
                    (exec_id, session_id, b"\x80"),
                )
            conn.commit()
        behind = rest_escalate_ok(
            ctx["url"], session_id, {"questions": [{"question": "Behind the run?"}]}
        )

        httpx.post(f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=10)
        assert done.wait(timeout=25), "stream did not close"

        body = "\n".join(lines)
        assert behind["event_id"] in body, (
            "the event behind the corrupt run never arrived"
        )


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_the_seam_has_no_cursor_above_the_highest_possible_id(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-max-id")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")
        with db_conn(test_database) as conn:
            conn.execute(
                "INSERT INTO events (id, execution_id, session_id, event_type, payload) "
                "VALUES (?, ?, ?, 'message', ?)",
                (
                    2**63 - 1,
                    exec_id,
                    session_id,
                    '{"role":"ROLE_AGENT","parts":[{"text":"at the top"}]}',
                ),
            )
            conn.commit()

        lines = []
        try:
            with httpx.stream(
                "GET",
                f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                timeout=4,
            ) as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines():
                    lines.append(line)
                    if line == "" and lines:
                        break
        except httpx.ReadTimeout:
            pass

        seam = json.loads(next(line[5:] for line in lines if line.startswith("data:")))
        assert seam["position"] == str(2**63 - 1)
        assert seam["history_before"] is None, "no cursor exists above the maximum id"

        page = httpx.get(
            f"{ctx['url']}/api/v1/sessions/{session_id}/events", timeout=10
        ).json()
        assert str(2**63 - 1) in [e["id"] for e in page["items"]]


@DUAL_BACKEND
def test_the_stream_ignores_every_query_parameter(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="sse-garbage-query")
        exec_id, _session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        url = (
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream"
            "?since=abc&x=%FF&=&&limit=-1"
        )
        lines = []
        try:
            with httpx.stream("GET", url, timeout=4) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                for line in resp.iter_lines():
                    lines.append(line)
                    if line == "":
                        break
        except httpx.ReadTimeout:
            pass

        assert any(line.startswith("event: position") for line in lines), lines
        seam = json.loads(next(line[5:] for line in lines if line.startswith("data:")))
        assert set(seam) == {"position", "history_before"}
