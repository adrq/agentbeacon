# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

import threading
import time

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    cleanup_processes,
    scheduler_context,
    start_worker,
)
from tests.mock_agent_helpers import seed_acp_mock_agent
from tests.sse_helpers import (
    _stream_sse_events,
    _persisted,
    _position,
)


def _rest_events(url: str, exec_id: str) -> list[dict]:
    resp = httpx.get(f"{url}/api/v1/executions/{exec_id}/events", timeout=5)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _run_to_settled(url: str, exec_id: str) -> str:
    deadline = time.time() + 30
    status = None
    while time.time() < deadline:
        resp = httpx.get(f"{url}/api/v1/executions/{exec_id}", timeout=5)
        status = resp.json()["execution"]["status"]
        if status in ("completed", "failed", "awaiting_input"):
            break
        time.sleep(0.5)
    assert status in ("completed", "failed", "awaiting_input"), (
        f"Execution did not reach terminal state within 30s, stuck at: {status}"
    )
    return status


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_opens_with_position_seam(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, _session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        events = _stream_sse_events(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
            timeout=3.0,
        )
        seam = _position(events)
        assert set(seam) == {"position", "history_before"}
        assert seam["position"] is not None
        assert _persisted(events) == []

        page = httpx.get(
            f"{ctx['url']}/api/v1/executions/{exec_id}/events",
            params={"before": seam["history_before"]},
            timeout=5,
        ).json()
        assert page["items"], "history page should not be empty"
        assert page["items"][-1]["id"] == seam["position"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_delivers_live_events(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        collected = []
        done = threading.Event()

        def collect():
            collected.extend(
                _stream_sse_events(
                    f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                    timeout=20.0,
                )
            )
            done.set()

        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(1.0)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/escalate",
            json={"questions": [{"question": "Live?"}], "importance": "blocking"},
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        escalation_id = resp.json()["event_id"]

        httpx.post(f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5)
        assert done.wait(timeout=20), "stream did not close on terminal"

        delivered = _persisted(collected)
        assert escalation_id in [e["data"]["id"] for e in delivered]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_ignores_replay_cursors(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, _session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            _run_to_settled(ctx["url"], exec_id)
            time.sleep(1.0)
            history = _rest_events(ctx["url"], exec_id)
            assert len(history) >= 3

            base = f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream"
            for connect in (
                lambda: _stream_sse_events(
                    base, headers={"Last-Event-ID": history[0]["id"]}, timeout=3.0
                ),
                lambda: _stream_sse_events(
                    f"{base}?since={history[0]['id']}", timeout=3.0
                ),
            ):
                events = connect()
                assert _persisted(events) == []
                assert _position(events)["position"] == history[-1]["id"]

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sse_terminal_event_closes_stream(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, _session_id = create_execution_via_api(
            ctx["url"], agent_id, "DELAY_10000"
        )

        worker = start_worker(ctx["url"], interval="500ms")
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=5)
                status = resp.json()["execution"]["status"]
                if status in ("working", "awaiting_input"):
                    break
                time.sleep(0.5)
            assert status in ("working", "awaiting_input")

            sse_events = []
            sse_done = threading.Event()

            def collect_sse():
                sse_events.extend(
                    _stream_sse_events(
                        f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                        timeout=30.0,
                    )
                )
                sse_done.set()

            t = threading.Thread(target=collect_sse, daemon=True)
            t.start()
            time.sleep(1)

            cancel_resp = httpx.post(
                f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5
            )
            assert cancel_resp.status_code == 200

            assert sse_done.wait(timeout=15), "SSE stream did not close after cancel"

            cancel_events = [
                e
                for e in _persisted(sse_events)
                if e["data"]["event_type"] == "state_change"
                and e["data"]["session_id"] is None
                and e["data"]["payload"].get("outcome") == "canceled"
            ]
            assert len(cancel_events) == 1, "the terminal event is delivered once"

        finally:
            cleanup_processes([worker])


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sse_nonexistent_execution_returns_problem_404(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(
            f"{ctx['url']}/api/v1/executions/nonexistent-id/events/stream",
            timeout=5,
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "resource.not_found"


@pytest.mark.parametrize("test_database", ["sqlite"], indirect=True)
def test_sse_event_format_matches_rest_api(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_acp_mock_agent(ctx["db_url"])
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "say hello"
        )

        collected = []
        done = threading.Event()

        def collect():
            collected.extend(
                _stream_sse_events(
                    f"{ctx['url']}/api/v1/executions/{exec_id}/events/stream",
                    timeout=20.0,
                )
            )
            done.set()

        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(1.0)

        resp = httpx.post(
            f"{ctx['url']}/api/v1/escalate",
            json={"questions": [{"question": "Same shape?"}], "importance": "blocking"},
            headers={"Authorization": f"Bearer {session_id}"},
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        escalation_id = resp.json()["event_id"]

        httpx.post(f"{ctx['url']}/api/v1/executions/{exec_id}/terminate", timeout=5)
        assert done.wait(timeout=20)

        streamed = {e["data"]["id"]: e for e in _persisted(collected)}
        assert escalation_id in streamed
        frame = streamed[escalation_id]
        assert frame["id"] == escalation_id

        rest = {e["id"]: e for e in _rest_events(ctx["url"], exec_id)}
        assert frame["data"] == rest[escalation_id]
