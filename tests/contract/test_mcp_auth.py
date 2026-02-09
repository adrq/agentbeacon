"""Contract tests for MCP endpoint auth, initialization, and transport compliance."""

import uuid

import httpx
import pytest

from tests.testhelpers import (
    create_execution_via_api,
    db_conn,
    mcp_call,
    mcp_raw,
    scheduler_context,
    seed_test_agent,
)


# --- Auth tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_missing_bearer_header_returns_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={"Accept": "application/json, text/event-stream"},
            timeout=5,
        )
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_invalid_session_id_returns_401(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        fake_token = str(uuid.uuid4())
        resp = mcp_raw(ctx["url"], fake_token, "initialize")
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_terminal_session_returns_404(test_database):
    """Completed sessions return 404 per MCP spec — session terminated, not auth failure."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = 'completed' WHERE id = ?",
                (session_id,),
            )
            conn.commit()

        resp = mcp_raw(ctx["url"], session_id, "initialize")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "canceled"])
def test_all_terminal_statuses_return_404(test_database, terminal_status):
    """All terminal session states return 404, not 401."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "UPDATE sessions SET status = ? WHERE id = ?",
                (terminal_status, session_id),
            )
            conn.commit()

        resp = mcp_raw(ctx["url"], session_id, "tools/list")
        assert resp.status_code == 404


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unknown_token_returns_401_not_404(test_database):
    """Unknown tokens are auth failures (401), not session termination (404)."""
    with scheduler_context(db_url=test_database) as ctx:
        fake_token = str(uuid.uuid4())
        resp = mcp_raw(ctx["url"], fake_token, "initialize")
        assert resp.status_code == 401


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_401_includes_www_authenticate_header(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={"Accept": "application/json, text/event-stream"},
            timeout=5,
        )
        assert resp.status_code == 401
        assert "www-authenticate" in resp.headers
        assert resp.headers["www-authenticate"].startswith("Bearer")


# --- Initialize tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_valid_master_token_returns_initialize_response(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "initialize")

        assert "result" in data
        result = data["result"]
        assert result["protocolVersion"] == "2025-11-25"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "agentbeacon"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_valid_child_token_returns_initialize_response(test_database):
    """Child sessions (those with parent_session_id) should also work."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        exec_id, master_session_id = create_execution_via_api(
            ctx["url"], agent_id, "test task"
        )

        child_session_id = str(uuid.uuid4())
        with db_conn(ctx["db_url"]) as conn:
            conn.execute(
                "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, 'submitted')",
                (child_session_id, exec_id, master_session_id, agent_id),
            )
            conn.commit()

        data = mcp_call(ctx["url"], child_session_id, "initialize")
        assert "result" in data
        assert data["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_initialize_includes_server_title(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "initialize")
        server_info = data["result"]["serverInfo"]
        assert "title" in server_info
        assert isinstance(server_info["title"], str)
        assert len(server_info["title"]) > 0


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_initialize_response_includes_session_id_header(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = mcp_raw(ctx["url"], session_id, "initialize")
        assert resp.status_code == 200
        assert "mcp-session-id" in resp.headers


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_initialize_version_negotiation(test_database):
    """Server responds with its supported version regardless of client request."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
                "id": 1,
            },
            headers={
                "Authorization": f"Bearer {session_id}",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        data = resp.json()
        assert data["result"]["protocolVersion"] == "2025-11-25"


# --- Notification tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_initialized_notification_returns_202(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = mcp_raw(ctx["url"], session_id, "notifications/initialized", rpc_id=None)
        assert resp.status_code == 202


# --- Method tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_unknown_method_returns_method_not_found(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "nonexistent/method")

        assert "error" in data
        assert data["error"]["code"] == -32601


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_ping_returns_empty_result(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "ping")
        assert "result" in data
        assert data["result"] == {}


# --- Transport compliance tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_get_mcp_returns_405(test_database):
    """Spec allows 405 for GET — SSE streaming deferred to Phase 2."""
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.get(f"{ctx['url']}/mcp", timeout=5)
        assert resp.status_code == 405


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_delete_mcp_returns_405(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        resp = httpx.delete(f"{ctx['url']}/mcp", timeout=5)
        assert resp.status_code == 405


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.com",
        "http://localhost.evil.com",
        "http://localhostevil.com",
        "http://127.0.0.1.evil.com",
        "https://localhost.attacker.io",
    ],
)
def test_invalid_origin_returns_403(test_database, origin):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={
                "Authorization": f"Bearer {session_id}",
                "Origin": origin,
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert resp.status_code == 403


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "http://localhost:5173",
        "http://localhost:9456",
        "https://localhost",
        "http://127.0.0.1",
        "http://127.0.0.1:3000",
        "http://[::1]",
        "http://[::1]:8080",
    ],
)
def test_valid_origin_succeeds(test_database, origin):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={
                "Authorization": f"Bearer {session_id}",
                "Origin": origin,
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert resp.status_code == 200


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_no_origin_header_succeeds(test_database):
    """Missing Origin is fine — only present-but-invalid triggers 403."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "initialize")
        assert "result" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_invalid_protocol_version_returns_400(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            headers={
                "Authorization": f"Bearer {session_id}",
                "MCP-Protocol-Version": "9999-01-01",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_valid_protocol_version_succeeds(test_database):
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={
                "Authorization": f"Bearer {session_id}",
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert resp.status_code == 200


# --- MCP-Session-Id mismatch tests ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_mismatched_mcp_session_id_returns_400(test_database):
    """If MCP-Session-Id is present but doesn't match Bearer token, reject with 400."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        wrong_session_id = str(uuid.uuid4())
        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            headers={
                "Authorization": f"Bearer {session_id}",
                "MCP-Session-Id": wrong_session_id,
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert resp.status_code == 400


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_matching_mcp_session_id_succeeds(test_database):
    """If MCP-Session-Id matches Bearer token, request proceeds normally."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={
                "Authorization": f"Bearer {session_id}",
                "MCP-Session-Id": session_id,
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_absent_mcp_session_id_succeeds(test_database):
    """Omitting MCP-Session-Id entirely is fine (e.g. first request)."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        data = mcp_call(ctx["url"], session_id, "initialize")
        assert "result" in data


# --- Lifecycle handshake test ---


@pytest.mark.parametrize("test_database", ["sqlite", "postgres"], indirect=True)
def test_full_lifecycle_initialize_then_tools_list(test_database):
    """Positive path: initialize → notifications/initialized → tools/list succeeds."""
    with scheduler_context(db_url=test_database) as ctx:
        agent_id = seed_test_agent(ctx["db_url"], name="claude-code")
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test task")

        # Step 1: initialize
        init_resp = mcp_raw(ctx["url"], session_id, "initialize")
        assert init_resp.status_code == 200
        init_data = init_resp.json()
        assert init_data["result"]["protocolVersion"] == "2025-11-25"
        mcp_session_id = init_resp.headers.get("mcp-session-id")
        assert mcp_session_id is not None

        # Step 2: notifications/initialized (no id = notification)
        notif_resp = mcp_raw(
            ctx["url"], session_id, "notifications/initialized", rpc_id=None
        )
        assert notif_resp.status_code == 202

        # Step 3: tools/list with MCP-Session-Id from initialize
        tools_resp = httpx.post(
            f"{ctx['url']}/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 2},
            headers={
                "Authorization": f"Bearer {session_id}",
                "MCP-Session-Id": mcp_session_id,
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            },
            timeout=5,
        )
        assert tools_resp.status_code == 200
        tools_data = tools_resp.json()
        assert "result" in tools_data
        tool_names = [t["name"] for t in tools_data["result"]["tools"]]
        assert "delegate" in tool_names
        assert "ask_user" in tool_names
