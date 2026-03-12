"""Contract tests for MCP servers CRUD and project junction endpoints."""

import json

import httpx
import pytest

from tests.testhelpers import (
    create_project_via_api,
    create_execution_via_api,
    db_conn,
    mcp_tools_call,
    scheduler_context,
    seed_test_agent,
)


def create_mcp_server_via_api(
    scheduler_url: str,
    name: str,
    transport_type: str = "stdio",
    config: dict = None,
) -> dict:
    if config is None:
        if transport_type == "stdio":
            config = {"command": "echo", "args": ["hello"]}
        else:
            config = {"url": "https://example.com/mcp"}

    resp = httpx.post(
        f"{scheduler_url}/api/mcp-servers",
        json={"name": name, "transport_type": transport_type, "config": config},
        timeout=5,
    )
    assert resp.status_code == 201, (
        f"create mcp server failed: {resp.status_code} {resp.text}"
    )
    return resp.json()


# --- MCP Servers CRUD ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_stdio(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        data = create_mcp_server_via_api(
            ctx["url"],
            "playwright",
            transport_type="stdio",
            config={
                "command": "npx",
                "args": ["@playwright/mcp"],
                "env": {"DISPLAY": ":1"},
            },
        )

        assert data["name"] == "playwright"
        assert data["transport_type"] == "stdio"
        assert data["config"]["command"] == "npx"
        assert data["config"]["args"] == ["@playwright/mcp"]
        assert data["config"]["env"] == {"DISPLAY": ":1"}
        assert "id" in data
        assert len(data["id"]) == 36
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_http(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        data = create_mcp_server_via_api(
            ctx["url"],
            "custom-api",
            transport_type="http",
            config={
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer tok"},
            },
        )

        assert data["name"] == "custom-api"
        assert data["transport_type"] == "http"
        assert data["config"]["url"] == "https://example.com/mcp"
        assert data["config"]["headers"] == {"Authorization": "Bearer tok"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_reserved_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "agentbeacon",
                "transport_type": "stdio",
                "config": {"command": "echo"},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "reserved" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_reserved_name_case_insensitive(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "AgentBeacon",
                "transport_type": "stdio",
                "config": {"command": "echo"},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "reserved" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_duplicate_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_mcp_server_via_api(ctx["url"], "unique-name")

        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "unique-name",
                "transport_type": "stdio",
                "config": {"command": "echo"},
            },
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_invalid_transport(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "bad",
                "transport_type": "grpc",
                "config": {"command": "echo"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_stdio_missing_command(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "bad",
                "transport_type": "stdio",
                "config": {"args": ["test"]},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "command" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_http_missing_url(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={"name": "bad", "transport_type": "http", "config": {"headers": {}}},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "url" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_stdio_args_must_be_strings(test_database):
    """args must be string[], not mixed types."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "bad",
                "transport_type": "stdio",
                "config": {"command": "npx", "args": [1, True]},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "args" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_stdio_env_must_be_string_map(test_database):
    """env must be Record<string,string>, not mixed types."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "bad",
                "transport_type": "stdio",
                "config": {"command": "npx", "env": {"PORT": 123}},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "env" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_http_headers_must_be_string_map(test_database):
    """headers must be Record<string,string>."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "bad",
                "transport_type": "http",
                "config": {"url": "https://example.com/mcp", "headers": []},
            },
            timeout=5,
        )
        assert resp.status_code == 400
        assert "headers" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_rejects_unknown_config_keys(test_database):
    """Unknown keys in config are rejected (additionalProperties: false)."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/api/mcp-servers",
            json={
                "name": "bad",
                "transport_type": "stdio",
                "config": {"command": "npx", "unknown_key": "value"},
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_mcp_servers(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_mcp_server_via_api(ctx["url"], "server-a")
        create_mcp_server_via_api(ctx["url"], "server-b")

        resp = httpx.get(f"{ctx['url']}/api/mcp-servers", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {s["name"] for s in data}
        assert names == {"server-a", "server-b"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_mcp_server(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_mcp_server_via_api(ctx["url"], "get-test")

        resp = httpx.get(f"{ctx['url']}/api/mcp-servers/{created['id']}", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == created["id"]
        assert data["name"] == "get-test"
        assert data["transport_type"] == "stdio"
        assert "config" in data
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_mcp_server_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/api/mcp-servers/nonexistent-id", timeout=5)
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_mcp_server(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_mcp_server_via_api(ctx["url"], "original")

        resp = httpx.patch(
            f"{ctx['url']}/api/mcp-servers/{created['id']}",
            json={"name": "updated"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "updated"
        assert "updated_at" in data
        assert data["updated_at"] >= created["updated_at"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_mcp_server_name_to_reserved(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_mcp_server_via_api(ctx["url"], "normal-name")

        resp = httpx.patch(
            f"{ctx['url']}/api/mcp-servers/{created['id']}",
            json={"name": "agentbeacon"},
            timeout=5,
        )
        assert resp.status_code == 400
        assert "reserved" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_mcp_server_transport_without_config(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_mcp_server_via_api(ctx["url"], "stdio-server")

        resp = httpx.patch(
            f"{ctx['url']}/api/mcp-servers/{created['id']}",
            json={"transport_type": "http"},
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_mcp_server_duplicate_name(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        create_mcp_server_via_api(ctx["url"], "existing-name")
        second = create_mcp_server_via_api(ctx["url"], "other-name")

        resp = httpx.patch(
            f"{ctx['url']}/api/mcp-servers/{second['id']}",
            json={"name": "existing-name"},
            timeout=5,
        )
        assert resp.status_code == 409


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_create_mcp_server_type_key_stripped(test_database):
    """The 'type' key in config should be stripped before storing."""
    with scheduler_context(db_url=test_database) as ctx:
        data = create_mcp_server_via_api(
            ctx["url"],
            "strip-test",
            transport_type="stdio",
            config={"command": "npx", "type": "stdio"},
        )

        resp = httpx.get(f"{ctx['url']}/api/mcp-servers/{data['id']}", timeout=5)
        assert resp.status_code == 200
        assert "type" not in resp.json()["config"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_update_mcp_server_change_transport_with_config(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_mcp_server_via_api(ctx["url"], "switch-server")

        resp = httpx.patch(
            f"{ctx['url']}/api/mcp-servers/{created['id']}",
            json={
                "transport_type": "http",
                "config": {"url": "https://example.com/mcp"},
            },
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["transport_type"] == "http"
        assert data["config"]["url"] == "https://example.com/mcp"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_mcp_server(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        created = create_mcp_server_via_api(ctx["url"], "to-delete")

        resp = httpx.delete(f"{ctx['url']}/api/mcp-servers/{created['id']}", timeout=5)
        assert resp.status_code == 204

        resp = httpx.get(f"{ctx['url']}/api/mcp-servers", timeout=5)
        assert len(resp.json()) == 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_mcp_server_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/api/mcp-servers/nonexistent-id", timeout=5)
        assert resp.status_code == 404


# --- Project MCP Servers Junction ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_mcp_server_to_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        resp = httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )
        assert resp.status_code == 204


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_list_project_mcp_servers(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["mcp_server_id"] == server["id"]
        assert data[0]["name"] == "playwright"
        assert "config" in data[0]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_mcp_server_from_project(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers/{server['id']}",
            timeout=5,
        )
        assert resp.status_code == 204

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            timeout=5,
        )
        assert resp.json() == []


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_add_mcp_server_to_project_idempotent(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        for _ in range(2):
            resp = httpx.post(
                f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
                json={"mcp_server_id": server["id"]},
                timeout=5,
            )
            assert resp.status_code in (200, 204)

        resp = httpx.get(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            timeout=5,
        )
        assert len(resp.json()) == 1


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_remove_mcp_server_from_project_not_attached(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers/{server['id']}",
            timeout=5,
        )
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_detach_then_delete_mcp_server(test_database):
    """Detach MCP server from project, then delete the server."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        # Detach first
        resp = httpx.delete(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers/{server['id']}",
            timeout=5,
        )
        assert resp.status_code == 204

        # Now delete the server (no attachments)
        resp = httpx.delete(f"{ctx['url']}/api/mcp-servers/{server['id']}", timeout=5)
        assert resp.status_code == 204


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_mcp_server_with_attachments(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        resp = httpx.delete(f"{ctx['url']}/api/mcp-servers/{server['id']}", timeout=5)
        assert resp.status_code == 409
        assert "attached" in resp.text.lower() or "detach" in resp.text.lower()


# --- Task Payload Threading ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_includes_mcp_servers(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(
            ctx["url"],
            "playwright",
            config={"command": "npx", "args": ["@playwright/mcp"]},
        )
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task", project_id=project["id"]
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert "mcp_servers" in payload
        assert "playwright" in payload["mcp_servers"]
        mcp_config = payload["mcp_servers"]["playwright"]
        assert mcp_config["type"] == "stdio"
        assert mcp_config["command"] == "npx"
        assert mcp_config["args"] == ["@playwright/mcp"]


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_no_mcp_servers_when_none_attached(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task", project_id=project["id"]
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert "mcp_servers" not in payload


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_task_payload_mcp_servers_format(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")

        stdio_server = create_mcp_server_via_api(
            ctx["url"],
            "stdio-server",
            transport_type="stdio",
            config={
                "command": "npx",
                "args": ["@playwright/mcp"],
                "env": {"DISPLAY": ":1"},
            },
        )
        http_server = create_mcp_server_via_api(
            ctx["url"],
            "http-server",
            transport_type="http",
            config={"url": "https://example.com/mcp", "headers": {"X-Key": "val"}},
        )

        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": stdio_server["id"]},
            timeout=5,
        )
        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": http_server["id"]},
            timeout=5,
        )

        exec_id, session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task", project_id=project["id"]
        )

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        payload = json.loads(row[0])
        mcp = payload["mcp_servers"]

        # stdio server
        assert mcp["stdio-server"]["type"] == "stdio"
        assert mcp["stdio-server"]["command"] == "npx"
        assert mcp["stdio-server"]["args"] == ["@playwright/mcp"]
        assert mcp["stdio-server"]["env"] == {"DISPLAY": ":1"}

        # http server
        assert mcp["http-server"]["type"] == "http"
        assert mcp["http-server"]["url"] == "https://example.com/mcp"
        assert mcp["http-server"]["headers"] == {"X-Key": "val"}


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_mcp_server_after_project_soft_deleted(test_database):
    """Soft-deleting a project should not block MCP server deletion."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        # Soft-delete the project
        resp = httpx.delete(f"{ctx['url']}/api/projects/{project['id']}", timeout=5)
        assert resp.status_code == 204

        # Junction row remains (soft-delete doesn't CASCADE), but delete
        # should succeed because the project is logically gone.
        resp = httpx.delete(f"{ctx['url']}/api/mcp-servers/{server['id']}", timeout=5)
        assert resp.status_code == 204


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_mcp_server_blocked_by_active_execution_on_deleted_project(
    test_database,
):
    """Cannot delete MCP server if a soft-deleted project still has active executions."""
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(ctx["url"], "playwright")
        agent_id = seed_test_agent(ctx["db_url"], name="test-agent")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        # Create an execution on this project (status = 'submitted', non-terminal)
        create_execution_via_api(
            ctx["url"], agent_id, "do work", project_id=project["id"]
        )

        # Soft-delete the project
        resp = httpx.delete(f"{ctx['url']}/api/projects/{project['id']}", timeout=5)
        assert resp.status_code == 204

        # Delete blocked — active execution still references this project
        resp = httpx.delete(f"{ctx['url']}/api/mcp-servers/{server['id']}", timeout=5)
        assert resp.status_code == 409
        assert "active executions" in resp.text.lower()


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delegate_task_payload_includes_mcp_servers(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        project = create_project_via_api(ctx["url"], "my-project")
        server = create_mcp_server_via_api(
            ctx["url"],
            "playwright",
            config={"command": "npx", "args": ["@playwright/mcp"]},
        )

        lead_agent_id = seed_test_agent(ctx["db_url"], name="lead-agent")
        child_agent_id = seed_test_agent(ctx["db_url"], name="child-agent")

        httpx.post(
            f"{ctx['url']}/api/projects/{project['id']}/mcp-servers",
            json={"mcp_server_id": server["id"]},
            timeout=5,
        )

        exec_id, lead_session_id = create_execution_via_api(
            ctx["url"], lead_agent_id, "coordinate task", project_id=project["id"]
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

        child_payload = json.loads(result["content"][0]["text"])
        child_session_id = child_payload["session_id"]

        with db_conn(ctx["db_url"]) as conn:
            row = conn.execute(
                "SELECT task_payload FROM task_queue WHERE session_id = ?",
                (child_session_id,),
            ).fetchone()

        assert row is not None
        payload = json.loads(row[0])
        assert "mcp_servers" in payload
        assert "playwright" in payload["mcp_servers"]
        assert payload["mcp_servers"]["playwright"]["type"] == "stdio"
        assert payload["mcp_servers"]["playwright"]["command"] == "npx"
