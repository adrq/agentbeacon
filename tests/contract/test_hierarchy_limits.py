"""Contract tests for hierarchy limits (max_depth, max_width) and 3-role enforcement.

Tests the expanded McpRole {RootLead, SubLead, Leaf} model, depth-based tool
surface filtering, max_width enforcement, and config-driven defaults.
"""

import json
import tempfile
import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    mcp_tools_list,
    scheduler_context,
    seed_test_agent,
)


def _create_child_session(
    ctx, parent_session_id, exec_id, agent_id, status="submitted"
):
    """Insert a child session directly into the DB."""
    child_id = str(uuid.uuid4())
    with db_conn(ctx["db_url"]) as conn:
        conn.execute(
            "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
            (child_id, exec_id, parent_session_id, agent_id, status),
        )
        conn.commit()
    return child_id


def _create_execution_with_limits(
    ctx, agent_id, prompt, max_depth=None, max_width=None
):
    """Create execution via API with custom hierarchy limits."""
    payload = {
        "agent_id": agent_id,
        "prompt": prompt,
        "title": prompt,
        "cwd": tempfile.gettempdir(),
    }
    if max_depth is not None:
        payload["max_depth"] = max_depth
    if max_width is not None:
        payload["max_width"] = max_width
    resp = httpx.post(f"{ctx['url']}/api/executions", json=payload, timeout=10)
    assert resp.status_code == 201, f"Create execution failed: {resp.text}"
    data = resp.json()
    return data["execution"]["id"], data["session_id"]


# --- Role enforcement ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_lead_gets_delegate_release_escalate(test_database):
    """Root lead (depth 0, max_depth 2) gets all 3 tools."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert sorted(tools) == ["delegate", "escalate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sub_lead_gets_delegate_release(test_database):
    """Sub-lead (depth 1, max_depth 2) gets delegate and release, no escalate."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)
        tools = mcp_tools_list(ctx["url"], child_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_gets_no_tools(test_database):
    """Leaf (depth 2, max_depth 2) gets empty tool list."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)
        grandchild_id = _create_child_session(ctx, child_id, exec_id, agent_id)
        tools = mcp_tools_list(ctx["url"], grandchild_id)
        assert tools == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_lead_at_max_depth_gets_escalate_only(test_database):
    """Root lead with max_depth=0 (corrupted data) gets escalate only."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        # Bypass API validation by inserting directly with max_depth=0
        exec_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO executions (id, context_id, status, input, max_depth, max_width) VALUES (?, ?, 'submitted', ?, 0, 5)",
                (exec_id, context_id, "test task"),
            )
            conn.execute(
                "INSERT INTO sessions (id, execution_id, agent_id, status) VALUES (?, ?, ?, 'submitted')",
                (session_id, exec_id, agent_id),
            )
            conn.commit()

        tools = mcp_tools_list(ctx["url"], session_id)
        assert tools == ["escalate"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_rejected_for_sub_lead(test_database):
    """Sub-lead cannot call escalate."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)
        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "escalate",
                "arguments": {"questions": [{"question": "hello?"}]},
            },
        )
        assert data["error"]["code"] == -32600
        assert "root lead" in data["error"]["message"].lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_rejected_for_leaf(test_database):
    """Leaf cannot call any tool."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)
        grandchild_id = _create_child_session(ctx, child_id, exec_id, agent_id)

        data = mcp_call(
            ctx["url"],
            grandchild_id,
            "tools/call",
            params={
                "name": "escalate",
                "arguments": {"questions": [{"question": "hello?"}]},
            },
        )
        assert data["error"]["code"] == -32600
        assert "no tools available" in data["error"]["message"].lower()


# --- Max depth enforcement ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_rejected_at_max_depth(test_database):
    """Agent at max_depth cannot delegate."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        seed_test_agent(ctx["db_url"], name="child-agent")

        # max_depth=1 means child at depth 1 is a leaf
        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=1
        )
        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)

        data = mcp_call(
            ctx["url"],
            child_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "child-agent", "prompt": "do work"},
            },
        )
        assert data["error"]["code"] == -32600
        assert "no tools available" in data["error"]["message"].lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_creates_child_at_correct_depth(test_database):
    """Delegated child has depth = parent depth + 1."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "test task"
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
            {"agent": "child-agent", "prompt": "do work"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        # Child at depth 1 with max_depth 2 is SubLead (has delegate+release)
        tools = mcp_tools_list(ctx["url"], child_session_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_at_max_depth_is_leaf(test_database):
    """Child at max_depth gets empty tool list (leaf)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=1
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "do work"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        # Child at depth 1 with max_depth 1 is Leaf
        tools = mcp_tools_list(ctx["url"], child_session_id)
        assert tools == []


# --- Max width enforcement ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_rejected_at_max_width(test_database):
    """Agent with max_width active children cannot delegate."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_width=1
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        # First delegation succeeds
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "first child"},
        )
        assert result.get("isError") is False

        # Second delegation should fail (max_width=1)
        data = mcp_call(
            ctx["url"],
            lead_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "child-agent", "prompt": "second child"},
            },
        )
        assert data["error"]["code"] == -32602
        assert "maximum active children" in data["error"]["message"].lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_succeeds_after_releasing_child(test_database):
    """After releasing a child, agent can delegate again (width freed)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_width=1
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        # Delegate first child
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "first child"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        # Set child to input-required so it can be released
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'input-required' WHERE id = ?",
                (child_id,),
            )
            conn.commit()

        # Release child
        mcp_tools_call(ctx["url"], lead_id, "release", {"session_id": child_id})

        # Now second delegation should succeed
        result2 = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "second child"},
        )
        assert result2.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_max_width_counts_only_active_children(test_database):
    """Completed/failed/canceled children don't count toward width."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_width=1
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        # Insert a completed child (should not count toward width)
        _create_child_session(ctx, lead_id, exec_id, agent_id, status="completed")
        # Insert a failed child
        _create_child_session(ctx, lead_id, exec_id, agent_id, status="failed")
        # Insert a canceled child
        _create_child_session(ctx, lead_id, exec_id, agent_id, status="canceled")

        # Delegation should succeed (no active children)
        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "new child"},
        )
        assert result.get("isError") is False


# --- Execution creation ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_uses_config_defaults(test_database):
    """Execution without max_depth/max_width uses config table defaults."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT max_depth, max_width FROM executions WHERE id = ?",
                (exec_id,),
            ).fetchone()
        assert row[0] == 2
        assert row[1] == 5


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_accepts_custom_limits(test_database):
    """Execution with explicit max_depth/max_width uses those values."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=5, max_width=10
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT max_depth, max_width FROM executions WHERE id = ?",
                (exec_id,),
            ).fetchone()
        assert row[0] == 5
        assert row[1] == 10


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_rejects_invalid_max_depth(test_database):
    """max_depth outside 1-10 range rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        cwd = tempfile.gettempdir()
        # Too low
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test", "cwd": cwd, "max_depth": 0},
            timeout=10,
        )
        assert resp.status_code == 400

        # Too high
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test", "cwd": cwd, "max_depth": 11},
            timeout=10,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_rejects_invalid_max_width(test_database):
    """max_width outside 1-50 range rejected."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        cwd = tempfile.gettempdir()
        # Too low
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test", "cwd": cwd, "max_width": 0},
            timeout=10,
        )
        assert resp.status_code == 400

        # Too high
        resp = httpx.post(
            f"{ctx['url']}/api/executions",
            json={"agent_id": agent_id, "prompt": "test", "cwd": cwd, "max_width": 51},
            timeout=10,
        )
        assert resp.status_code == 400


# --- Config API ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_config_returns_max_depth_default(test_database):
    """GET /api/config includes max_depth entry."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/config", timeout=10)
        assert resp.status_code == 200
        configs = resp.json()
        config_map = {c["name"]: c["value"] for c in configs}
        assert "max_depth" in config_map
        assert config_map["max_depth"] == "2"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_config_update_max_depth(test_database):
    """POST /api/config updates max_depth default."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/config",
            json={"name": "max_depth", "value": "3"},
            timeout=10,
        )
        assert resp.status_code == 200

        # Verify new execution uses updated default
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT max_depth FROM executions WHERE id = ?",
                (exec_id,),
            ).fetchone()
        assert row[0] == 3


# --- Removed tools ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_tool_not_in_tool_list(test_database):
    """handoff no longer appears in tools/list for any role."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")
        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)

        lead_tools = mcp_tools_list(ctx["url"], lead_id)
        child_tools = mcp_tools_list(ctx["url"], child_id)
        assert "handoff" not in lead_tools
        assert "handoff" not in child_tools


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_next_instruction_tool_not_in_tool_list(test_database):
    """next_instruction no longer appears in tools/list for any role."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")
        child_id = _create_child_session(ctx, lead_id, exec_id, agent_id)

        lead_tools = mcp_tools_list(ctx["url"], lead_id)
        child_tools = mcp_tools_list(ctx["url"], child_id)
        assert "next_instruction" not in lead_tools
        assert "next_instruction" not in child_tools


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_call_returns_unknown_tool(test_database):
    """Calling handoff returns unknown tool error."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "handoff",
                "arguments": {"message": "done"},
            },
        )
        assert data["error"]["code"] == -32602
        assert "unknown tool" in data["error"]["message"].lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_next_instruction_call_returns_unknown_tool(test_database):
    """Calling next_instruction returns unknown tool error."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(
            ctx["url"],
            session_id,
            "tools/call",
            params={
                "name": "next_instruction",
                "arguments": {},
            },
        )
        assert data["error"]["code"] == -32602
        assert "unknown tool" in data["error"]["message"].lower()


# --- Execution response includes limits ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_response_includes_limits(test_database):
    """GET /api/executions/{id} includes max_depth and max_width."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=3, max_width=7
        )

        resp = httpx.get(f"{ctx['url']}/api/executions/{exec_id}", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["max_depth"] == 3
        assert data["execution"]["max_width"] == 7
