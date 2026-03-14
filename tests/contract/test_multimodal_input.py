"""Contract tests for multimodal input (parts with file attachments)."""

import json
import tempfile

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)

# Valid 1x1 pixel transparent PNG in base64.
SMALL_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="


def _text_part(text):
    return {"kind": "text", "text": text}


def _file_part(name="test.png", mime="image/png", b64=SMALL_PNG_B64):
    return {"kind": "file", "file": {"name": name, "mimeType": mime, "bytes": b64}}


def _set_session_status(db_url, session_id, status):
    """Directly set session status in DB for test setup."""
    with db_conn(db_url) as conn:
        conn.execute(
            "UPDATE sessions SET status = ? WHERE id = ?",
            (status, session_id),
        )
        conn.commit()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_execution_with_image_part(test_database):
    """POST /api/executions with text + file parts stores both in session events."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [_text_part("describe this image"), _file_part()],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        data = resp.json()
        session_id = data["session_id"]

        # Fetch session events and find the initial prompt message event
        events_resp = httpx.get(
            f"{ctx['url']}/api/sessions/{session_id}/events", timeout=5
        )
        assert events_resp.status_code == 200
        events = events_resp.json()

        msg_events = [e for e in events if e["event_type"] == "message"]
        assert len(msg_events) >= 1

        parts = msg_events[0]["payload"]["parts"]
        kinds = [p["kind"] for p in parts]
        assert "text" in kinds
        assert "file" in kinds

        file_parts = [p for p in parts if p["kind"] == "file"]
        assert file_parts[0]["file"]["name"] == "test.png"
        assert file_parts[0]["file"]["mimeType"] == "image/png"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_post_message_with_image_part(test_database):
    """POST /api/sessions/{id}/message with image part returns 200."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        # Transition session to input-required via escalate
        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "need image?"}], "importance": "blocking"},
        )

        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={
                "parts": [
                    _text_part("here is the screenshot"),
                    _file_part("screenshot.png"),
                ],
            },
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_empty_parts_rejected(test_database):
    """POST /api/executions with empty parts array returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")

        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [],
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_lateral_message_preserves_file_parts(test_database):
    """POST /api/messages with file part returns 200."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        # Delegate to create a child session
        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child", "prompt": "do work"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        # Get child's hierarchical name from session discovery
        disc_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/sessions", timeout=5
        )
        child_hier_name = None
        for entry in disc_resp.json():
            if entry["session_id"] == child_session_id:
                child_hier_name = entry["hierarchical_name"]
                break
        assert child_hier_name is not None

        # Put child into input-required so it can receive a lateral message
        _set_session_status(ctx["db_url"], child_session_id, "input-required")

        resp = httpx.post(
            f"{ctx['url']}/api/messages",
            json={
                "to": child_hier_name,
                "parts": [
                    _text_part("here is the file"),
                    _file_part("data.png"),
                ],
            },
            headers={"Authorization": f"Bearer {lead_session_id}"},
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_file_part_reaches_task_queue(test_database):
    """Parts with file attachments are preserved in the task queue payload."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")

        parts = [_text_part("analyze this"), _file_part("chart.png")]
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": parts,
                "cwd": tempfile.gettempdir(),
            },
            timeout=5,
        )
        assert resp.status_code == 201
        session_id = resp.json()["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])

        # The task payload should contain the parts with the file data
        task_parts = payload.get("parts", payload.get("message", {}).get("parts", []))
        kinds = [p["kind"] for p in task_parts]
        assert "text" in kinds
        assert "file" in kinds

        file_parts = [p for p in task_parts if p["kind"] == "file"]
        assert file_parts[0]["file"]["name"] == "chart.png"
        assert file_parts[0]["file"]["bytes"] == SMALL_PNG_B64


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_messages_returns_parts(test_database):
    """GET /api/messages returns full parts including file parts."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child", "prompt": "do work"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        disc_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/sessions", timeout=5
        )
        child_hier_name = None
        for entry in disc_resp.json():
            if entry["session_id"] == child_session_id:
                child_hier_name = entry["hierarchical_name"]
                break
        assert child_hier_name is not None

        _set_session_status(ctx["db_url"], child_session_id, "input-required")

        # Send a message with text + file parts
        httpx.post(
            f"{ctx['url']}/api/messages",
            json={
                "to": child_hier_name,
                "parts": [
                    _text_part("check this image"),
                    _file_part("screenshot.png"),
                ],
            },
            headers={"Authorization": f"Bearer {lead_session_id}"},
            timeout=5,
        )

        # Read messages back via GET /api/messages
        msgs_resp = httpx.get(
            f"{ctx['url']}/api/messages",
            params={"session_id": child_session_id},
            timeout=5,
        )
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        assert len(messages) >= 1

        msg = messages[-1]
        # body should contain the text
        assert "check this image" in msg["body"]
        # parts should contain both text and file parts (no internal data parts)
        kinds = [p["kind"] for p in msg["parts"]]
        assert "text" in kinds
        assert "file" in kinds
        # Verify no data parts leak through
        assert "data" not in kinds
        # Verify file content is preserved
        file_parts = [p for p in msg["parts"] if p["kind"] == "file"]
        assert file_parts[0]["file"]["name"] == "screenshot.png"
        assert file_parts[0]["file"]["bytes"] == SMALL_PNG_B64


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_session_message_rejects_no_deliverable_content(test_database):
    """POST /api/sessions/{id}/message with only data parts returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "task")

        mcp_tools_call(
            ctx["url"],
            session_id,
            "escalate",
            {"questions": [{"question": "need input?"}], "importance": "blocking"},
        )

        # Send parts with only a data part (no usable content)
        resp = httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"parts": [{"kind": "data", "data": {"type": "meta"}}]},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_lateral_message_rejects_no_deliverable_content(test_database):
    """POST /api/messages with only data parts returns 400."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child")
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child", "prompt": "do work"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        disc_resp = httpx.get(
            f"{ctx['url']}/api/executions/{exec_id}/sessions", timeout=5
        )
        child_hier_name = None
        for entry in disc_resp.json():
            if entry["session_id"] == child_session_id:
                child_hier_name = entry["hierarchical_name"]
                break
        assert child_hier_name is not None

        _set_session_status(ctx["db_url"], child_session_id, "input-required")

        # Send parts with only a data part (no usable content)
        resp = httpx.post(
            f"{ctx['url']}/api/messages",
            json={
                "to": child_hier_name,
                "parts": [{"kind": "data", "data": {"type": "meta"}}],
            },
            headers={"Authorization": f"Bearer {lead_session_id}"},
            timeout=5,
        )
        assert resp.status_code == 400
