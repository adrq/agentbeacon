"""Contract tests for initial prompt events recorded on session creation."""

import json

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def parse_payload(raw):
    """Parse event payload — may be a JSON string or already-deserialized dict."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_session_has_initial_prompt_event(test_database):
    """Root session's first event is a message with the execution prompt."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "implement auth module"
        )

        resp = httpx.get(f"{ctx['url']}/api/sessions/{session_id}/events", timeout=5)
        assert resp.status_code == 200
        events = resp.json()

        msg_events = [e for e in events if e["event_type"] == "message"]
        assert len(msg_events) >= 1
        first_msg = msg_events[0]
        payload = parse_payload(first_msg["payload"])
        assert payload["role"] == "user"
        assert payload["parts"][0]["kind"] == "text"
        assert payload["parts"][0]["text"] == "implement auth module"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_session_prompt_event_has_no_sender(test_database):
    """Root session prompt event has no sender data part (renders as 'You')."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = httpx.get(f"{ctx['url']}/api/sessions/{session_id}/events", timeout=5)
        events = resp.json()
        msg_events = [e for e in events if e["event_type"] == "message"]
        payload = parse_payload(msg_events[0]["payload"])
        assert len(payload["parts"]) == 1
        assert payload["parts"][0]["kind"] == "text"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_child_session_has_initial_prompt_event_with_sender(test_database):
    """Delegated child session's first event is a message attributed to the parent."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_id, "coordinate"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        resp = httpx.get(
            f"{ctx['url']}/api/sessions/{child_session_id}/events", timeout=5
        )
        assert resp.status_code == 200
        events = resp.json()
        msg_events = [e for e in events if e["event_type"] == "message"]
        assert len(msg_events) >= 1

        payload = parse_payload(msg_events[0]["payload"])
        assert payload["role"] == "user"
        assert len(payload["parts"]) == 2
        sender_part = payload["parts"][0]
        assert sender_part["kind"] == "data"
        assert sender_part["data"]["type"] == "sender"
        assert "name" in sender_part["data"]
        assert sender_part["data"]["session_id"] == lead_session_id
        text_part = payload["parts"][1]
        assert text_part["kind"] == "text"
        assert text_part["text"] == "implement auth"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_empty_prompt_rejected(test_database):
    """Empty prompt is rejected at API level (400) — no session or event created."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "", "cwd": "/tmp"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_prompt_event_precedes_state_change(test_database):
    """Prompt event is recorded before the execution state_change event."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}/events", timeout=5)
        assert resp.status_code == 200
        events = resp.json()

        prompt_event = next(
            (
                e
                for e in events
                if e["event_type"] == "message" and e.get("session_id") == session_id
            ),
            None,
        )
        state_change = next(
            (e for e in events if e["event_type"] == "state_change"),
            None,
        )
        assert prompt_event is not None
        assert state_change is not None
        assert prompt_event["id"] < state_change["id"]
