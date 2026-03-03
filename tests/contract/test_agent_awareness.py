"""Contract tests for agent environment awareness — briefing, env vars, pool validation."""

import json
import tempfile
import uuid

import pytest
import requests

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_includes_project_id(test_database):
    """Root lead task_payload should include project_id when execution has one."""
    with scheduler_context(db_url=test_database) as ctx:
        # Create a project first
        project_id = None
        with db_conn(ctx["db_url"]) as conn:
            project_id = str(uuid.uuid4())
            project_path = tempfile.gettempdir()
            conn.execute(
                "INSERT INTO projects (id, name, path) VALUES (?, ?, ?)",
                (project_id, "test-project", project_path),
            )
            conn.commit()

        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task", project_id=project_id
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert payload["project_id"] == project_id


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_has_system_prompt_with_briefing(test_database):
    """Root lead task_payload system_prompt should contain environment briefing."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")

        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        assert "AgentBeacon Environment" in system_prompt
        assert "root lead" in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_rejects_agent_not_in_pool(test_database):
    """Delegate should reject agents not in the execution's agent pool."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        # Create outsider agent but do NOT include in execution pool
        seed_test_agent(ctx["db_url"], name="outsider-agent")

        _, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "test task"
        )

        data = mcp_call(
            ctx["url"],
            lead_session_id,
            "tools/call",
            params={
                "name": "delegate",
                "arguments": {"agent": "outsider-agent", "prompt": "do work"},
            },
        )

        assert data["error"]["code"] == -32602
        assert "not available in this execution" in data["error"]["message"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_gets_briefing(test_database):
    """Child agent's task_payload should contain environment briefing."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        # Add child-agent to the execution pool
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
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        assert "AgentBeacon Environment" in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_child_is_sub_lead(test_database):
    """With default max_depth=2, a depth-1 child should be sub-lead."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        # Default max_depth=2, root is depth 0, child is depth 1 → sub-lead
        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()

        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        assert "sub-lead" in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_leaf_briefing_omits_delegation(test_database):
    """Leaf agent briefing should not contain delegation section."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        # Create execution with max_depth=1, so first child is a leaf
        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )
        # Override max_depth to 1 so children are immediately leaves
        # Also add child-agent to execution pool
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE executions SET max_depth = 1 WHERE id = ?",
                (exec_id,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        result = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result["content"][0]["text"])["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()

        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        assert "leaf" in system_prompt
        assert "## Delegation" not in system_prompt


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_api_docs_endpoint(test_database):
    """GET /api/docs should return markdown API reference without auth."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = requests.get(f"{ctx['url']}/api/docs")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        assert "AgentBeacon REST API Reference" in resp.text
        assert "POST /api/messages" in resp.text
        assert "revision_number" in resp.text


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_resumed_session_gets_briefing(test_database):
    """Resumed session should also get environment briefing."""
    with scheduler_context(db_url=test_database) as ctx:
        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task"
        )
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execution_agents (execution_id, agent_id) VALUES (?, ?)",
                (exec_id, child_agent_id),
            )
            conn.commit()

        # First delegation
        result1 = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {"agent": "child-agent", "prompt": "implement auth"},
        )
        child_session_id = json.loads(result1["content"][0]["text"])["session_id"]

        # Mark child completed so it can be resumed
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed' WHERE id = ?",
                (child_session_id,),
            )
            # Drain the first task_queue entry
            conn.execute(
                "DELETE FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            )
            conn.commit()

        # Resume
        result2 = mcp_tools_call(
            ctx["url"],
            lead_session_id,
            "delegate",
            {
                "agent": "child-agent",
                "prompt": "continue work",
                "session_id": child_session_id,
            },
        )
        resumed_id = json.loads(result2["content"][0]["text"])["session_id"]
        assert resumed_id == child_session_id

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        system_prompt = payload["agent_config"]["system_prompt"]
        assert "AgentBeacon Environment" in system_prompt
