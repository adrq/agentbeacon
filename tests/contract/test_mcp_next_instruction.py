"""Contract tests for MCP next_instruction tool."""

import json
import sqlite3
import threading
import time
import uuid

import httpx

from tests.testhelpers import (
    create_execution_via_api,
    mcp_tools_call,
    scheduler_context,
)


def _seed_agent_with_poll_timeout(db_path, name="test-agent", poll_timeout_ms=1000):
    """Seed an agent with a custom poll_timeout_ms in config."""
    agent_id = str(uuid.uuid4())
    config = json.dumps({"poll_timeout_ms": poll_timeout_ms})
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO agents (id, name, agent_type, config, enabled) VALUES (?, ?, 'claude_sdk', ?, 1)",
            (agent_id, name, config),
        )
        conn.commit()
    finally:
        conn.close()
    return agent_id


def _mcp_call_long(url, token, method, params=None, rpc_id=1, timeout=10):
    """MCP call with configurable timeout for long-poll tests."""
    body = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
    if params is not None:
        body["params"] = params
    resp = httpx.post(
        f"{url}/mcp",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": "2025-11-25",
            "Accept": "application/json, text/event-stream",
        },
        timeout=timeout,
    )
    return resp.json()


def _call_next_instruction(url, token, timeout=10):
    """Call next_instruction tool via MCP, return parsed task payload."""
    data = _mcp_call_long(
        url,
        token,
        "tools/call",
        params={"name": "next_instruction", "arguments": {}},
        timeout=timeout,
    )
    result = data.get("result", {})
    content = result.get("content", [])
    assert len(content) == 1
    return json.loads(content[0]["text"])


def _create_child_session(ctx, agent_id, master_id, exec_id, status="submitted"):
    """Create a child session directly in DB."""
    child_id = str(uuid.uuid4())
    conn = sqlite3.connect(ctx["db_path"])
    conn.execute(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status) VALUES (?, ?, ?, ?, ?)",
        (child_id, exec_id, master_id, agent_id, status),
    )
    conn.commit()
    conn.close()
    return child_id


def test_next_instruction_timeout_returns_timed_out():
    """Empty inbox → blocks for poll_timeout_ms then returns timed_out: true."""
    with scheduler_context() as ctx:
        agent_id = _seed_agent_with_poll_timeout(ctx["db_path"], poll_timeout_ms=1000)
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Inbox is empty (create_execution doesn't enqueue tasks)
        start = time.monotonic()
        payload = _call_next_instruction(ctx["url"], session_id, timeout=10)
        elapsed = time.monotonic() - start

        assert payload["timed_out"] is True
        assert elapsed >= 0.9, (
            f"Should have blocked ~1s, but returned in {elapsed:.2f}s"
        )


def test_next_instruction_returns_queued_task_immediately():
    """Task already in inbox → returns immediately without waiting."""
    with scheduler_context() as ctx:
        agent_id = _seed_agent_with_poll_timeout(ctx["db_path"], poll_timeout_ms=5000)
        exec_id, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Manually enqueue a task so the inbox is non-empty
        conn = sqlite3.connect(ctx["db_path"])
        payload = json.dumps({"kind": "test_task", "message": "hello"})
        conn.execute(
            "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
            (exec_id, session_id, payload),
        )
        conn.commit()
        conn.close()

        start = time.monotonic()
        result = _call_next_instruction(ctx["url"], session_id, timeout=10)
        elapsed = time.monotonic() - start

        assert "task" in result
        assert result["task"]["kind"] == "test_task"
        assert elapsed < 2.0, f"Should return immediately, took {elapsed:.2f}s"


def test_child_next_instruction_gets_initial_prompt():
    """Child session: delegate puts task in child inbox → child next_instruction gets it."""
    with scheduler_context() as ctx:
        master_agent_id = _seed_agent_with_poll_timeout(
            ctx["db_path"], name="master", poll_timeout_ms=5000
        )
        child_agent_id = _seed_agent_with_poll_timeout(
            ctx["db_path"], name="child", poll_timeout_ms=5000
        )
        _, master_sid = create_execution_via_api(ctx["url"], master_agent_id, "plan")

        # Master delegates to child
        delegate_result = mcp_tools_call(
            ctx["url"], master_sid, "delegate", {"agent": "child", "prompt": "do work"}
        )
        content = json.loads(delegate_result["content"][0]["text"])
        child_sid = content["session_id"]

        # Child calls next_instruction → gets the delegated task
        payload = _call_next_instruction(ctx["url"], child_sid, timeout=10)
        assert "task" in payload
        task = payload["task"]
        assert task["agent_id"] == child_agent_id
        parts = task["message"]["parts"]
        assert any(p.get("text") == "do work" for p in parts)


def test_master_next_instruction_gets_handoff_result():
    """Full round-trip: delegate → child handoff → master next_instruction gets result."""
    with scheduler_context() as ctx:
        master_agent_id = _seed_agent_with_poll_timeout(
            ctx["db_path"], name="master", poll_timeout_ms=5000
        )
        _seed_agent_with_poll_timeout(
            ctx["db_path"], name="child", poll_timeout_ms=5000
        )
        exec_id, master_sid = create_execution_via_api(
            ctx["url"], master_agent_id, "plan"
        )

        # Drain master's initial task from queue
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (master_sid,))
        conn.commit()
        conn.close()

        # Master delegates
        delegate_result = mcp_tools_call(
            ctx["url"], master_sid, "delegate", {"agent": "child", "prompt": "do work"}
        )
        child_sid = json.loads(delegate_result["content"][0]["text"])["session_id"]

        # Child drains its task
        _call_next_instruction(ctx["url"], child_sid, timeout=10)

        # Update child to working so handoff is valid
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "UPDATE sessions SET status = 'working' WHERE id = ?", (child_sid,)
        )
        conn.commit()
        conn.close()

        # Child hands off
        mcp_tools_call(ctx["url"], child_sid, "handoff", {"message": "done with work"})

        # Master next_instruction → should get handoff result
        payload = _call_next_instruction(ctx["url"], master_sid, timeout=10)
        assert "task" in payload
        task = payload["task"]
        assert task["kind"] == "handoff_result"
        assert task["child_session_id"] == child_sid
        assert task["message"] == "done with work"


def test_ask_user_blocking_then_answer_via_next_instruction():
    """Master ask_user(blocking) → user answers via REST → master next_instruction gets answer."""
    with scheduler_context() as ctx:
        agent_id = _seed_agent_with_poll_timeout(ctx["db_path"], poll_timeout_ms=5000)
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Drain initial task
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()

        # Update session to working
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "UPDATE sessions SET status = 'working' WHERE id = ?", (session_id,)
        )
        conn.commit()
        conn.close()

        # Master asks a blocking question
        mcp_tools_call(
            ctx["url"],
            session_id,
            "ask_user",
            {"question": "which approach?", "importance": "blocking"},
        )

        # User answers via REST
        httpx.post(
            f"{ctx['url']}/api/sessions/{session_id}/message",
            json={"message": "use approach B"},
            timeout=5,
        )

        # Master next_instruction → gets user_answer
        payload = _call_next_instruction(ctx["url"], session_id, timeout=10)
        assert "task" in payload
        task = payload["task"]
        assert task["kind"] == "user_answer"
        assert task["message"] == "use approach B"


def test_concurrent_next_instruction_wakes_on_handoff():
    """Master blocks on next_instruction in background, child handoff wakes it."""
    with scheduler_context() as ctx:
        master_agent_id = _seed_agent_with_poll_timeout(
            ctx["db_path"], name="master", poll_timeout_ms=10000
        )
        _seed_agent_with_poll_timeout(
            ctx["db_path"], name="child", poll_timeout_ms=5000
        )
        exec_id, master_sid = create_execution_via_api(
            ctx["url"], master_agent_id, "plan"
        )

        # Drain master's initial task
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute("DELETE FROM task_queue WHERE session_id = ?", (master_sid,))
        conn.commit()
        conn.close()

        # Delegate to child
        delegate_result = mcp_tools_call(
            ctx["url"], master_sid, "delegate", {"agent": "child", "prompt": "do work"}
        )
        child_sid = json.loads(delegate_result["content"][0]["text"])["session_id"]

        # Child drains its task
        _call_next_instruction(ctx["url"], child_sid, timeout=10)

        # Set child to working
        conn = sqlite3.connect(ctx["db_path"])
        conn.execute(
            "UPDATE sessions SET status = 'working' WHERE id = ?", (child_sid,)
        )
        conn.commit()
        conn.close()

        # Master blocks on next_instruction in a thread
        result_holder = [None]
        start_time = [None]

        def master_wait():
            start_time[0] = time.monotonic()
            result_holder[0] = _call_next_instruction(
                ctx["url"], master_sid, timeout=15
            )

        t = threading.Thread(target=master_wait)
        t.start()

        # Give master time to enter long-poll
        time.sleep(0.5)

        # Child handoff — should wake master
        mcp_tools_call(ctx["url"], child_sid, "handoff", {"message": "all done"})

        t.join(timeout=10)
        assert not t.is_alive(), "Master thread should have returned"

        elapsed = time.monotonic() - start_time[0]
        assert elapsed < 5.0, f"Master should wake quickly, took {elapsed:.2f}s"

        payload = result_holder[0]
        assert "task" in payload
        assert payload["task"]["kind"] == "handoff_result"


def test_coordination_mode_set_on_first_call():
    """First next_instruction call sets coordination_mode to mcp_poll."""
    with scheduler_context() as ctx:
        agent_id = _seed_agent_with_poll_timeout(ctx["db_path"], poll_timeout_ms=1000)
        _, session_id = create_execution_via_api(ctx["url"], agent_id, "test")

        # Verify initial mode is sdk
        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT coordination_mode FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "sdk"

        # Call next_instruction (it'll return the queued task immediately)
        _call_next_instruction(ctx["url"], session_id, timeout=10)

        # Verify mode changed to mcp_poll
        conn = sqlite3.connect(ctx["db_path"])
        row = conn.execute(
            "SELECT coordination_mode FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "mcp_poll"
