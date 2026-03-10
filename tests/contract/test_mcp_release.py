"""Contract tests for MCP release tool."""

import json
import uuid

import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def _create_child_session(ctx, lead_session_id, exec_id, agent_id, status="submitted"):
    """Insert a child session directly into the DB."""
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (child_id, exec_id, lead_session_id, agent_id, status),
        )
        conn.commit()
    return child_id


def _create_grandchild_session(ctx, parent_id, exec_id, agent_id, status="submitted"):
    """Insert a grandchild session directly into the DB."""
    gc_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (gc_id, exec_id, parent_id, agent_id, status),
        )
        conn.commit()
    return gc_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_input_required_child_completes_it(test_database):
    """Release transitions child from input-required to completed."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["released"] is True
        assert payload["sessions_terminated"] == 1

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()
            # Verify release platform event logged on lead (parent) session
            events = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'platform' ORDER BY created_at DESC",
                (lead_sid,),
            ).fetchall()
        assert row[0] == "completed"
        parsed_events = [json.loads(r[0]) for r in events]
        release_events = [
            e
            for e in parsed_events
            if any(
                p.get("kind") == "data" and p.get("data", {}).get("type") == "release"
                for p in e.get("parts", [])
            )
        ]
        assert len(release_events) > 0, (
            f"No release platform event found on lead session. Events: {parsed_events}"
        )


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_cascades_to_grandchildren(test_database):
    """Release on a sub-lead cascades to its children."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        gc_id = _create_grandchild_session(
            ctx, child_id, exec_id, agent_id, status="input-required"
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        payload = json.loads(result["content"][0]["text"])
        # child (input-required→completed) + grandchild (input-required→completed)
        assert payload["sessions_terminated"] == 2

        with db_conn(ctx["db_url"]) as conn:
            child_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()[0]
            gc_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_id,)
            ).fetchone()[0]

        assert child_status == "completed"
        assert gc_status == "completed"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_cascade_working_child_gets_canceled(test_database):
    """Working descendants get canceled, not completed."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        gc_id = _create_grandchild_session(
            ctx, child_id, exec_id, agent_id, status="working"
        )

        mcp_tools_call(ctx["url"], lead_sid, "release", {"session_id": child_id})

        with db_conn(ctx["db_url"]) as conn:
            gc_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_id,)
            ).fetchone()[0]

        assert gc_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_cascade_submitted_child_gets_canceled(test_database):
    """Submitted descendants (queued, never started) get canceled."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        gc_id = _create_grandchild_session(
            ctx, child_id, exec_id, agent_id, status="submitted"
        )

        mcp_tools_call(ctx["url"], lead_sid, "release", {"session_id": child_id})

        with db_conn(ctx["db_url"]) as conn:
            gc_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_id,)
            ).fetchone()[0]

        assert gc_status == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_requires_parent_authority(test_database):
    """Release fails if caller is not the parent."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        # Create a child that belongs to a different parent (i.e., not lead_sid)
        other_parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            # Create another session as another parent
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'working')",
                (other_parent_id, exec_id, lead_sid, agent_id),
            )
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'input-required')",
                (child_id, exec_id, other_parent_id, agent_id),
            )
            conn.commit()

        data = mcp_call(
            ctx["url"],
            lead_sid,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": child_id}},
        )

        assert data["error"]["code"] == -32602
        assert "not a child" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_rejects_sibling(test_database):
    """Sibling cannot release sibling (not a child of the caller)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        sibling_a = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        sibling_b = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )

        data = mcp_call(
            ctx["url"],
            sibling_a,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": sibling_b}},
        )

        assert data["error"]["code"] == -32602
        # SubLead has release tool, but sibling_b is not its child → authority check fails
        assert "not a child" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_working_child_cancels_it(test_database):
    """Release on a working child cancels it (parent can pivot at any time)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["released"] is True

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()
            event_row = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change' ORDER BY id DESC LIMIT 1",
                (child_id,),
            ).fetchone()
        assert row[0] == "canceled"
        assert event_row is not None, "no state_change event for working→canceled"
        event_payload = json.loads(event_row[0])
        assert event_payload["from"] == "working"
        assert event_payload["to"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_submitted_child_cancels_it(test_database):
    """Release on a submitted child cancels it."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="submitted"
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        assert result.get("isError") is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["released"] is True

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()
            event_row = conn.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'state_change' ORDER BY id DESC LIMIT 1",
                (child_id,),
            ).fetchone()
        assert row[0] == "canceled"
        assert event_row is not None, "no state_change event for submitted→canceled"
        event_payload = json.loads(event_row[0])
        assert event_payload["from"] == "submitted"
        assert event_payload["to"] == "canceled"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_working_root_with_mixed_descendants(test_database):
    """Releasing a working child cascades correctly to mixed-state descendants."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="working"
        )
        gc_working = _create_grandchild_session(
            ctx, child_id, exec_id, agent_id, status="working"
        )
        gc_input_req = _create_grandchild_session(
            ctx, child_id, exec_id, agent_id, status="input-required"
        )
        gc_completed = _create_grandchild_session(
            ctx, child_id, exec_id, agent_id, status="completed"
        )

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        payload = json.loads(result["content"][0]["text"])
        # child (working→canceled) + gc_working (→canceled) + gc_input_req (→completed) = 3
        # gc_completed is already terminal → skipped
        assert payload["sessions_terminated"] == 3

        with db_conn(ctx["db_url"]) as conn:
            child_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (child_id,)
            ).fetchone()[0]
            working_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_working,)
            ).fetchone()[0]
            input_req_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_input_req,)
            ).fetchone()[0]
            completed_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (gc_completed,)
            ).fetchone()[0]

        assert child_status == "canceled"
        assert working_status == "canceled"
        assert input_req_status == "completed"
        assert completed_status == "completed"  # unchanged


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "canceled"])
@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_rejects_already_terminal(test_database, terminal_status):
    """Release fails if target is already in a terminal state."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status=terminal_status
        )

        data = mcp_call(
            ctx["url"],
            lead_sid,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": child_id}},
        )

        assert data["error"]["code"] == -32602
        assert "terminal" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_skips_already_terminal_descendants(test_database):
    """Already-terminal descendants are not affected by cascade."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        _create_grandchild_session(ctx, child_id, exec_id, agent_id, status="completed")
        _create_grandchild_session(ctx, child_id, exec_id, agent_id, status="failed")

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        payload = json.loads(result["content"][0]["text"])
        # Only the child itself should be terminated (grandchildren already terminal)
        assert payload["sessions_terminated"] == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_release_returns_correct_count(test_database):
    """Return value includes count of actually terminated sessions."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, lead_sid = create_execution_via_api(ctx["url"], agent_id, "test")

        child_id = _create_child_session(
            ctx, lead_sid, exec_id, agent_id, status="input-required"
        )
        _create_grandchild_session(ctx, child_id, exec_id, agent_id, status="working")
        _create_grandchild_session(ctx, child_id, exec_id, agent_id, status="submitted")
        _create_grandchild_session(ctx, child_id, exec_id, agent_id, status="completed")

        result = mcp_tools_call(
            ctx["url"], lead_sid, "release", {"session_id": child_id}
        )

        payload = json.loads(result["content"][0]["text"])
        # child (input-required→completed) + 2 active grandchildren = 3
        # completed grandchild is skipped
        assert payload["sessions_terminated"] == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sub_lead_cannot_release_parent(test_database):
    """Sub-lead has release but cannot release its parent (not a child of caller)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_id, exec_id, lead_id, agent_id),
            )
            conn.commit()

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={"name": "release", "arguments": {"session_id": lead_id}},
        )

        assert data["error"]["code"] == -32602
        # SubLead has release tool, but lead_id is not its child → authority check
        assert "not a child" in data["error"]["message"]
