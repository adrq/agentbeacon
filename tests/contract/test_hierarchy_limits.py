# SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

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
from tests.mock_agent_helpers import (
    create_child_session,
    rest_escalate,
)


def _create_execution_with_limits(
    ctx, agent_id, prompt, max_depth=None, max_width=None
):
    payload = {
        "root_agent_id": agent_id,
        "agent_ids": [agent_id],
        "parts": [{"text": prompt}],
        "title": prompt,
        "cwd": tempfile.gettempdir(),
    }
    if max_depth is not None:
        payload["max_depth"] = max_depth
    if max_width is not None:
        payload["max_width"] = max_width
    resp = httpx.post(f"{ctx['url']}/api/v1/executions", json=payload, timeout=10)
    assert resp.status_code == 201, f"Create execution failed: {resp.text}"
    data = resp.json()
    return data["execution"]["id"], data["session_id"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_lead_gets_delegate_release(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        tools = mcp_tools_list(ctx["url"], session_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_sub_lead_gets_delegate_release(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)
        tools = mcp_tools_list(ctx["url"], child_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_gets_no_tools(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=2
        )

        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)
        grandchild_id = create_child_session(ctx["db_url"], child_id, exec_id, agent_id)
        tools = mcp_tools_list(ctx["url"], grandchild_id)
        assert tools == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_root_lead_at_max_depth_gets_no_tools(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                'INSERT INTO executions (id, context_id, max_depth, max_width, sandbox_policy) VALUES (?, ?, 0, 5, \'{"fs_level":"unrestricted"}\')',
                (exec_id, context_id),
            )
            conn.execute(
                'INSERT INTO sessions (id, execution_id, agent_id, sandbox_policy) VALUES (?, ?, ?, \'{"fs_level":"unrestricted"}\')',
                (session_id, exec_id, agent_id),
            )
            conn.commit()

        tools = mcp_tools_list(ctx["url"], session_id)
        assert tools == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_rejected_for_sub_lead(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)
        resp = rest_escalate(
            ctx["url"],
            child_id,
            {"questions": [{"question": "hello?"}]},
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_escalate_rejected_for_leaf(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=2
        )

        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)
        grandchild_id = create_child_session(ctx["db_url"], child_id, exec_id, agent_id)

        resp = rest_escalate(
            ctx["url"],
            grandchild_id,
            {"questions": [{"question": "hello?"}]},
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_rejected_at_max_depth(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_id = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=1
        )
        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)

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

        tools = mcp_tools_list(ctx["url"], child_session_id)
        assert sorted(tools) == ["delegate", "release"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_at_max_depth_is_leaf(test_database):
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

        tools = mcp_tools_list(ctx["url"], child_session_id)
        assert tools == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_rejected_at_max_width(test_database):
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

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "first child"},
        )
        assert result.get("isError") is False

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

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "first child"},
        )
        child_id = json.loads(result["content"][0]["text"])["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET desired = 'run', executor_state = 'idle' WHERE id = ?",
                (child_id,),
            )
            conn.commit()

        mcp_tools_call(ctx["url"], lead_id, "release", {"session_id": child_id})

        result2 = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "second child"},
        )
        assert result2.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_max_width_counts_only_active_children(test_database):
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

        create_child_session(
            ctx["db_url"], lead_id, exec_id, agent_id, status="completed"
        )
        create_child_session(ctx["db_url"], lead_id, exec_id, agent_id, status="failed")
        create_child_session(
            ctx["db_url"], lead_id, exec_id, agent_id, status="canceled"
        )

        result = mcp_tools_call(
            ctx["url"],
            lead_id,
            "delegate",
            {"agent": "child-agent", "prompt": "new child"},
        )
        assert result.get("isError") is False


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_uses_config_defaults(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT max_depth, max_width FROM executions WHERE id = ?",
                (exec_id,),
            ).fetchone()
        assert row[0] == 5
        assert row[1] == 10


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_accepts_custom_limits(test_database):
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
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        cwd = tempfile.gettempdir()
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": cwd,
                "max_depth": 0,
            },
            timeout=10,
        )
        assert resp.status_code == 400

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": cwd,
                "max_depth": 11,
            },
            timeout=10,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_creation_rejects_invalid_max_width(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        cwd = tempfile.gettempdir()
        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": cwd,
                "max_width": 0,
            },
            timeout=10,
        )
        assert resp.status_code == 400

        resp = httpx.post(
            f"{ctx['url']}/api/v1/executions",
            json={
                "root_agent_id": agent_id,
                "agent_ids": [agent_id],
                "parts": [{"text": "test"}],
                "cwd": cwd,
                "max_width": 51,
            },
            timeout=10,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_config_returns_max_depth_default(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/config", timeout=10)
        assert resp.status_code == 200
        configs = resp.json()
        config_map = {c["name"]: c["value"] for c in configs}
        assert "max_depth" in config_map
        assert config_map["max_depth"] == "5"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_config_update_max_depth(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/v1/config",
            json={"name": "max_depth", "value": "3"},
            timeout=10,
        )
        assert resp.status_code == 200

        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT max_depth FROM executions WHERE id = ?",
                (exec_id,),
            ).fetchone()
        assert row[0] == 3


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_tool_not_in_tool_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")
        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)

        lead_tools = mcp_tools_list(ctx["url"], lead_id)
        child_tools = mcp_tools_list(ctx["url"], child_id)
        assert "handoff" not in lead_tools
        assert "handoff" not in child_tools


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_next_instruction_tool_not_in_tool_list(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, lead_id = create_execution_via_api(ctx["url"], agent_id, "test task")
        child_id = create_child_session(ctx["db_url"], lead_id, exec_id, agent_id)

        lead_tools = mcp_tools_list(ctx["url"], lead_id)
        child_tools = mcp_tools_list(ctx["url"], child_id)
        assert "next_instruction" not in lead_tools
        assert "next_instruction" not in child_tools


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_handoff_call_returns_unknown_tool(test_database):
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


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_execution_response_includes_limits(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")
        exec_id, _ = _create_execution_with_limits(
            ctx, agent_id, "test task", max_depth=3, max_width=7
        )

        resp = httpx.get(f"{ctx['url']}/api/v1/executions/{exec_id}", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution"]["max_depth"] == 3
        assert data["execution"]["max_width"] == 7


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_config_returns_updated_max_width_default(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/config", timeout=10)
        assert resp.status_code == 200
        configs = resp.json()
        config_map = {c["name"]: c["value"] for c in configs}
        assert "max_width" in config_map
        assert config_map["max_width"] == "10"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_config_has_use_db_overrides_flag(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/v1/config", timeout=10)
        assert resp.status_code == 200
        configs = resp.json()
        config_map = {c["name"]: c["value"] for c in configs}
        assert "briefing.use_db_overrides" in config_map
        assert config_map["briefing.use_db_overrides"] == "false"
